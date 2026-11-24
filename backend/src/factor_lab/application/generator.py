"""Synthetic deal generator driven by distribution parameters.

Every distribution the portfolio is built from is a parameter: remaining maturity buckets,
payment types (annuity / regular principal / bullet), fixed or floating rate, payment
frequency, amounts, rates, the share of subsidised deals and of prepayment models. Deals are
written in the `deals.csv` format and pass cfcalc validation (deal date not after the report
date, maturity after it, floating rates on CBKEY1M within its history, no day number for
day-based periods).

The file is reproducible: the same parameters and seed give the same deals whatever the
number of worker processes, because every chunk seeds its own generator from its first id.
"""

from __future__ import annotations

import math
import multiprocessing
import os
import random
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

COLUMNS = (
    "report_dttm;discriminator;deal_id;currency_id;start_principal_amt;current_principal_amt;"
    "deal_dttm;maturity_dttm;rate_type_cd;interest_rate;base_indicator_rate;base_indicator;"
    "interest_payment_rule_start_dttm;interest_payment_rule_period_cd;interest_payment_rule_day_num;"
    "day_convention_cd;regular_payment_rule_start_dttm;regular_payment_rule_period_cd;"
    "regular_payment_rule_day_num;repricing_rule_start_dttm;repricing_rule_period_cd;"
    "repricing_rule_day_num;accrued_interest;product;behavior_model_cd;interest_payment;"
    "principal_payment;repricing_dttm_schedule;repricing_dttm_rate_schedule;"
    "repricing_dttm_rate_schedule_fixed;cap_limit_interest_rate;floor_limit_interest_rate;"
    "sub_rate_type_cd;sub_interest_rate;sub_float_indicator;sub_payment_rule_period_cd;"
    "sub_payment_rule_day_num;sub_payment_rule_start_dttm;sub_repricing_rule_start_dttm;"
    "sub_repricing_rule_period_cd;sub_repricing_rule_day_num;sub_cap_limit_interest_rate;"
    "sub_floor_limit_interest_rate;sub_coeff_index;deal_report_dttm;"
    "sub_repricing_rule_first_event_flg;indicator_lag_cd;sub_indicator_lag_cd"
).split(";")
IDX = {name: i for i, name in enumerate(COLUMNS)}

PAYMENT_TYPES = ("ANN", "RGM", "SIMPLE")
RATE_TYPES = ("FIXED", "FLOAT")
FREQUENCIES = ("1M", "3M", "6M", "12M")
INDICATOR = "CBKEY1M"
# cashflow-rs keeps CBKEY1M from 2013-09-17: floating deals must start later
EARLIEST_FLOAT = date(2014, 1, 1)
MAX_ROWS = 3_000_000
CHUNK = 20_000


@dataclass
class MaturityBucket:
    """Remaining term to maturity, in years: deals in [min_years, max_years) with this share."""
    min_years: float
    max_years: float
    share: float
    label: str = ""


@dataclass
class Range:
    min: float
    max: float


@dataclass
class GeneratorParams:
    rows: int = 100_000
    report_date: str = "2022-08-31"
    seed: int = 42
    currency: dict[str, float] = field(default_factory=lambda: {"RUB": 0.99, "USD": 0.01})
    maturity: list[MaturityBucket] = field(default_factory=lambda: [
        MaturityBucket(0.08, 1, 0.25, "<1Y"),
        MaturityBucket(1, 3, 0.30, "1–3Y"),
        MaturityBucket(3, 5, 0.15, "3–5Y"),
        MaturityBucket(5, 10, 0.15, "5–10Y"),
        MaturityBucket(10, 30, 0.15, ">10Y"),
    ])
    payment_types: dict[str, float] = field(default_factory=lambda: {"ANN": 0.6, "RGM": 0.15, "SIMPLE": 0.25})
    rate_types: dict[str, float] = field(default_factory=lambda: {"FIXED": 0.8, "FLOAT": 0.2})
    payment_frequency: dict[str, float] = field(default_factory=lambda: {"1M": 0.75, "3M": 0.2, "12M": 0.05})
    amount: Range = field(default_factory=lambda: Range(50_000, 50_000_000))
    fixed_rate: Range = field(default_factory=lambda: Range(0.05, 0.18))
    float_spread: Range = field(default_factory=lambda: Range(0.0, 0.05))
    # share of RUB loans (ANN / RGM) with a subsidy leg: CBKEY1M + spread paid on top of the client rate
    subsidy_share: float = 0.1
    subsidy_spread: Range = field(default_factory=lambda: Range(0.02, 0.06))
    # behaviour model codes by share of deals; "" leaves the deal without a model
    prepayment: dict[str, float] = field(default_factory=lambda: {
        "PREPAYMENT_MORTGAGE": 0.2, "PREPAYMENT_CONSUMER": 0.2, "PREPAYMENT_AUTO": 0.05,
        "PREPAYMENT_MR8_CRED": 0.1, "PREPAYMENT_MR8_DEP": 0.05, "": 0.4,
    })

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GeneratorParams":
        d = dict(data)
        if "maturity" in d:
            d["maturity"] = [b if isinstance(b, MaturityBucket) else MaturityBucket(**b) for b in d["maturity"]]
        for key in ("amount", "fixed_rate", "float_spread", "subsidy_spread"):
            if key in d and not isinstance(d[key], Range):
                d[key] = Range(**d[key])
        params = cls(**d)
        params.validate()
        return params

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not 1 <= self.rows <= MAX_ROWS:
            raise ValueError(f"rows: 1 to {MAX_ROWS:,}")
        date.fromisoformat(self.report_date)
        _shares("currency", self.currency, None)
        _shares("payment_types", self.payment_types, PAYMENT_TYPES)
        _shares("rate_types", self.rate_types, RATE_TYPES)
        _shares("payment_frequency", self.payment_frequency, FREQUENCIES)
        _shares("prepayment", self.prepayment, None)
        if not self.maturity:
            raise ValueError("maturity: at least one bucket is required")
        for b in self.maturity:
            if not 0 < b.min_years < b.max_years <= 50:
                raise ValueError(f"maturity: bucket {b.min_years}–{b.max_years}Y is invalid")
            if b.share < 0:
                raise ValueError("maturity: shares cannot be negative")
        if sum(b.share for b in self.maturity) <= 0:
            raise ValueError("maturity: shares must sum above zero")
        for name, r, lo, hi in (("amount", self.amount, 1, 1e12), ("fixed_rate", self.fixed_rate, -0.05, 1),
                                ("float_spread", self.float_spread, -0.05, 0.5),
                                ("subsidy_spread", self.subsidy_spread, -0.05, 0.5)):
            if not lo <= r.min <= r.max <= hi:
                raise ValueError(f"{name}: need min ≤ max within [{lo}, {hi}]")
        if not 0 <= self.subsidy_share <= 1:
            raise ValueError("subsidy_share: must be between 0 and 1")


def _shares(name: str, shares: dict[str, float], allowed: tuple[str, ...] | None) -> None:
    if not shares:
        raise ValueError(f"{name}: empty distribution")
    for k, v in shares.items():
        if allowed is not None and k not in allowed:
            raise ValueError(f"{name}: unknown value {k!r}, allowed: {', '.join(allowed)}")
        if v < 0 or not math.isfinite(v):
            raise ValueError(f"{name}: shares cannot be negative")
    if sum(shares.values()) <= 0:
        raise ValueError(f"{name}: shares must sum above zero")


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _money(x: float) -> str:
    return f"{x:.2f}"


def _rate(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


class _Choice:
    def __init__(self, shares: dict[str, float]):
        self.keys = list(shares)
        self.weights = [shares[k] for k in self.keys]

    def pick(self, rng: random.Random) -> str:
        return rng.choices(self.keys, self.weights)[0]


class DealMaker:
    def __init__(self, p: GeneratorParams, rng: random.Random):
        self.p = p
        self.rng = rng
        self.report = date.fromisoformat(p.report_date)
        self.currency = _Choice(p.currency)
        self.payment = _Choice(p.payment_types)
        self.rate_type = _Choice(p.rate_types)
        self.frequency = _Choice(p.payment_frequency)
        self.model = _Choice(p.prepayment)
        self.buckets = p.maturity
        self.bucket_weights = [b.share for b in p.maturity]

    def _uniform(self, r: Range) -> float:
        return self.rng.uniform(r.min, r.max)

    def _amount(self) -> float:
        lo, hi = max(self.p.amount.min, 1.0), max(self.p.amount.max, 1.0)
        return lo * (hi / lo) ** self.rng.random()  # log-uniform: small deals outnumber large ones

    def _dates(self, floating: bool) -> tuple[date, date]:
        rng = self.rng
        bucket = rng.choices(self.buckets, self.bucket_weights)[0]
        remaining_days = max(31, round(rng.uniform(bucket.min_years, bucket.max_years) * 365))
        maturity = self.report + timedelta(days=remaining_days)
        # the deal ran for a while already: original term = remaining + elapsed
        elapsed_days = round(remaining_days * rng.uniform(0.05, 1.5)) + rng.randint(1, 60)
        start = self.report - timedelta(days=elapsed_days)
        if floating and start < EARLIEST_FLOAT:
            start = EARLIEST_FLOAT + timedelta(days=rng.randint(0, 365))
            start = min(start, self.report - timedelta(days=1))
        return start, maturity

    def row(self, deal_id: int) -> list[str]:
        rng, p = self.rng, self.p
        r = [""] * len(COLUMNS)
        ccy = self.currency.pick(rng)
        kind = self.payment.pick(rng)
        floating = self.rate_type.pick(rng) == "FLOAT"
        freq = self.frequency.pick(rng)
        start, maturity = self._dates(floating)
        principal = self._amount()
        day = start.day if start.day <= 28 else -1

        r[IDX["report_dttm"]] = r[IDX["deal_report_dttm"]] = self.report.isoformat()
        r[IDX["deal_id"]] = str(deal_id)
        r[IDX["currency_id"]] = ccy
        r[IDX["discriminator"]] = kind
        r[IDX["deal_dttm"]] = start.isoformat()
        r[IDX["maturity_dttm"]] = maturity.isoformat()
        r[IDX["day_convention_cd"]] = rng.choice(("ACT/365", "ACT/ACT ISDA"))
        r[IDX["interest_payment_rule_start_dttm"]] = start.isoformat()
        r[IDX["interest_payment_rule_period_cd"]] = freq
        r[IDX["interest_payment_rule_day_num"]] = str(day)
        r[IDX["regular_payment_rule_start_dttm"]] = start.isoformat()

        total = (maturity - start).days or 1
        passed = (self.report - start).days / total
        if kind == "SIMPLE":
            outstanding = principal
            r[IDX["regular_payment_rule_period_cd"]] = "ENDDATE"
        else:
            outstanding = principal * max(0.05, 1 - passed * rng.uniform(0.6, 1.0))
            r[IDX["regular_payment_rule_period_cd"]] = freq
            r[IDX["regular_payment_rule_day_num"]] = str(day)
        r[IDX["start_principal_amt"]] = _money(principal)
        r[IDX["current_principal_amt"]] = _money(outstanding)

        if floating:
            r[IDX["rate_type_cd"]] = "FLOAT"
            r[IDX["interest_rate"]] = _rate(self._uniform(p.float_spread))
            r[IDX["base_indicator"]] = INDICATOR
            r[IDX["repricing_rule_start_dttm"]] = start.isoformat()
            r[IDX["repricing_rule_period_cd"]] = rng.choice(("1M", "1M", "3M"))
            r[IDX["repricing_rule_day_num"]] = str(rng.choice((1, 15, -1)))
        else:
            r[IDX["rate_type_cd"]] = "FIXED"
            rate = self._uniform(p.fixed_rate)
            r[IDX["interest_rate"]] = _rate(max(0.005, rate - 0.05) if ccy != "RUB" else rate)

        r[IDX["product"]] = self._product(kind, maturity, start, principal)
        r[IDX["behavior_model_cd"]] = self.model.pick(rng)

        if ccy == "RUB" and kind != "SIMPLE" and rng.random() < p.subsidy_share:
            sub_start = max(start, EARLIEST_FLOAT)
            r[IDX["sub_rate_type_cd"]] = "FLOAT"
            r[IDX["sub_interest_rate"]] = _rate(self._uniform(p.subsidy_spread))
            r[IDX["sub_float_indicator"]] = INDICATOR
            r[IDX["sub_payment_rule_period_cd"]] = freq
            r[IDX["sub_payment_rule_day_num"]] = str(day)
            r[IDX["sub_payment_rule_start_dttm"]] = sub_start.isoformat()
            r[IDX["sub_repricing_rule_start_dttm"]] = sub_start.isoformat()
            r[IDX["sub_repricing_rule_period_cd"]] = "1M"
            r[IDX["sub_repricing_rule_day_num"]] = "-1"
            r[IDX["sub_repricing_rule_first_event_flg"]] = "1"
        return r

    @staticmethod
    def _product(kind: str, maturity: date, start: date, principal: float) -> str:
        years = (maturity - start).days / 365
        if kind == "ANN":
            return "mortgage" if years >= 7 else "consumer_loans"
        if kind == "RGM" or principal >= 10_000_000:
            return "loans_corporate"
        return "deposits_individual"


def _chunk(args: tuple[dict[str, Any], int, int]) -> str:
    params, first, count = args
    p = GeneratorParams.from_dict(params)
    maker = DealMaker(p, random.Random(p.seed * 1_000_003 + first))
    return "".join(";".join(maker.row(i)) + "\n" for i in range(first, first + count))


def generate(params: GeneratorParams, out: Path, progress: Callable[[int], None] | None = None,
             workers: int | None = None) -> int:
    """Writes the portfolio to `out` (via a temporary file) and returns its size in bytes."""
    params.validate()
    raw = params.to_dict()
    jobs = [(raw, first, min(CHUNK, params.rows - first + 1)) for first in range(1, params.rows + 1, CHUNK)]
    tmp = out.with_suffix(out.suffix + ".part")
    out.parent.mkdir(parents=True, exist_ok=True)
    done = 0
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(";".join(COLUMNS) + "\n")
        workers = max(1, min(workers or os.cpu_count() or 1, len(jobs)))
        chunks: Iterator[str]
        if workers == 1:
            chunks = map(_chunk, jobs)
            for text, job in zip(chunks, jobs):
                f.write(text)
                done += job[2]
                if progress:
                    progress(done)
        else:
            # spawn, not fork: the API process is multi-threaded and fork could copy held locks
            with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
                for text, job in zip(pool.map(_chunk, jobs), jobs):
                    f.write(text)
                    done += job[2]
                    if progress:
                        progress(done)
    tmp.replace(out)
    return out.stat().st_size
