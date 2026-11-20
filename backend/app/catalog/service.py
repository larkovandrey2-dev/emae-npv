from __future__ import annotations
import csv, json, os, tempfile, threading
from uuid import uuid4
from pathlib import Path
from typing import Any
from decimal import Decimal, InvalidOperation
from ..config import FIXTURES, RUNTIME, ASSESSMENT_DATE

FIELDS = next(
    csv.reader(
        [
            "report_dttm;discriminator;deal_id;currency_id;start_principal_amt;current_principal_amt;deal_dttm;maturity_dttm;rate_type_cd;interest_rate;base_indicator_rate;base_indicator;interest_payment_rule_start_dttm;interest_payment_rule_period_cd;interest_payment_rule_day_num;day_convention_cd;regular_payment_rule_start_dttm;regular_payment_rule_period_cd;regular_payment_rule_day_num;repricing_rule_start_dttm;repricing_rule_period_cd;repricing_rule_day_num;accrued_interest;product;behavior_model_cd;interest_payment;principal_payment;repricing_dttm_schedule;repricing_dttm_rate_schedule;repricing_dttm_rate_schedule_fixed;cap_limit_interest_rate;floor_limit_interest_rate;sub_rate_type_cd;sub_interest_rate;sub_float_indicator;sub_payment_rule_period_cd;sub_payment_rule_day_num;sub_payment_rule_start_dttm;sub_repricing_rule_start_dttm;sub_repricing_rule_period_cd;sub_repricing_rule_day_num;sub_cap_limit_interest_rate;sub_floor_limit_interest_rate;sub_coeff_index;deal_report_dttm;sub_repricing_rule_first_event_flg;indicator_lag_cd;sub_indicator_lag_cd"
        ],
        delimiter=";",
    )
)


def eligible_fields(f: dict[str, str]) -> tuple[bool, list[str]]:
    reasons = []
    if f.get("currency_id") != "RUB":
        reasons.append("Только RUB")
    try:
        principal = Decimal(f.get("current_principal_amt") or "0")
        if not principal.is_finite() or principal <= 0:
            reasons.append("Нет положительного остатка")
    except (InvalidOperation, ValueError):
        reasons.append("Некорректный остаток")
    try:
        rate = Decimal(f.get("interest_rate") or "NaN")
        if not rate.is_finite():
            reasons.append("Некорректная ставка")
    except (InvalidOperation, ValueError):
        reasons.append("Некорректная ставка")
    if f.get("rate_type_cd") != "FIXED":
        reasons.append("Ставка не FIXED")
    if f.get("report_dttm") != ASSESSMENT_DATE:
        reasons.append("Дата оценки отличается от 31.08.2022")
    try:
        if (f.get("maturity_dttm") or "")[:10] <= ASSESSMENT_DATE:
            reasons.append("Срок не позже даты оценки")
    except Exception:
        reasons.append("Некорректная дата окончания")
    for key, label in [
        ("sub_rate_type_cd", "Дополнительная процентная часть"),
        ("cap_limit_interest_rate", "Ограничение cap"),
        ("floor_limit_interest_rate", "Ограничение floor"),
        ("principal_payment", "Явный график тела"),
        ("interest_payment", "Явный график процентов"),
        ("repricing_dttm_schedule", "Расписание переоценки ставки"),
        ("repricing_dttm_rate_schedule", "Расписание ставок"),
        ("repricing_dttm_rate_schedule_fixed", "Расписание ставок"),
    ]:
        if (f.get(key) or "").strip():
            reasons.append(label)
    return not reasons, reasons


def scheme_compatible(f: dict[str, str], eligible: bool) -> bool:
    return bool(
        eligible
        and f.get("interest_payment_rule_start_dttm")
        and f.get("interest_payment_rule_period_cd") == "1M"
        and f.get("regular_payment_rule_start_dttm")
        and f.get("regular_payment_rule_period_cd") == "1M"
        and not (f.get("interest_payment") or "").strip()
        and not (f.get("principal_payment") or "").strip()
    )


def available_factors(f: dict[str, str], eligible: bool, scheme_ok: bool) -> list[str]:
    if not eligible:
        return []
    result = ["amount", "rate", "term", "day_count"]
    if (f.get("accrued_interest") or "").strip():
        result.append("accrued_interest")
    if scheme_ok:
        result.append("scheme")
    # Payment date is a shared parameter only for templates with both regular
    # monthly rules and no explicit schedule that could mask either rule.
    monthly_rules = all(
        f.get(f"{prefix}_start_dttm")
        and f.get(f"{prefix}_period_cd", "").endswith("M")
        and f.get(f"{prefix}_day_num")
        for prefix in ("interest_payment_rule", "regular_payment_rule")
    )
    if monthly_rules and scheme_ok:
        result.append("payment_day")
    # SIMPLE pays principal at maturity, so this frequency changes only the
    # actual regular interest-payment schedule. Other schemes have coupled
    # principal dates and are excluded from this one-factor control.
    if (
        f.get("discriminator") == "SIMPLE"
        and f.get("interest_payment_rule_start_dttm")
        and f.get("interest_payment_rule_period_cd", "").endswith("M")
        and f.get("interest_payment_rule_day_num")
        and not (f.get("interest_payment") or "").strip()
    ):
        result.append("payment_frequency")
    return result


def normalize(
    f: dict[str, str],
    ident: str | None = None,
    name: str | None = None,
    editable: bool | None = None,
) -> dict[str, Any]:
    ok, reasons = eligible_fields(f)
    deal_id = ident or f.get("deal_id", "")
    scheme_ok = scheme_compatible(f, ok) if editable is None else editable
    return {
        "id": deal_id,
        "name": name or f"Сделка {deal_id}",
        "current_principal_amt": f.get("current_principal_amt", ""),
        "interest_rate": f.get("interest_rate", ""),
        "maturity_dttm": (f.get("maturity_dttm") or "")[:10],
        "discriminator": f.get("discriminator", "ANN"),
        "report_dttm": f.get("report_dttm", ASSESSMENT_DATE),
        "currency_id": f.get("currency_id", ""),
        "day_convention_cd": f.get("day_convention_cd", ""),
        "eligible": ok,
        "restrictions": reasons,
        "scheme_editable": bool(scheme_ok),
        "available_factors": available_factors(f, ok, bool(scheme_ok)),
        "fields": dict(f),
    }


class Catalog:
    def __init__(self, runtime: Path | None = None):
        root = runtime or RUNTIME
        root.mkdir(parents=True, exist_ok=True)
        self.runtime = root
        self.path = root / "catalog.json"
        self.items: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        with (FIXTURES / "deals.csv").open(encoding="utf-8-sig", newline="") as h:
            for f in csv.DictReader(h, delimiter=";"):
                d = normalize(f)
                self.items[d["id"]] = d
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            for ident, item in saved.items():
                # Rebuild derived capabilities for older persisted catalogue rows.
                self.items[ident] = normalize(
                    item.get("fields", {}), ident, item.get("name"),
                    bool(item.get("scheme_editable")),
                )

    def save(self):
        content = json.dumps(
            {k: v for k, v in self.items.items() if k.startswith("new-")},
            ensure_ascii=False,
            indent=2,
        )
        fd, tmp = tempfile.mkstemp(prefix="catalog-", dir=self.runtime, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def list(self):
        with self._lock:
            return list(self.items.values())

    def get(self, ident):
        with self._lock:
            return self.items.get(str(ident))

    def create(self, data):
        with self._lock:
            return self._create(data)

    def _create(self, data):
        ident = f"new-{uuid4()}"
        end = data.maturity_dttm.isoformat()
        f = {k: "" for k in FIELDS}
        f.update(
            {
                "report_dttm": ASSESSMENT_DATE,
                "discriminator": data.discriminator,
                "deal_id": str(1000 + len(self.items)),
                "currency_id": "RUB",
                "current_principal_amt": data.current_principal_amt,
                "start_principal_amt": data.current_principal_amt,
                "deal_dttm": ASSESSMENT_DATE,
                "maturity_dttm": end,
                "rate_type_cd": "FIXED",
                "interest_rate": data.interest_rate,
                "interest_payment_rule_start_dttm": ASSESSMENT_DATE,
                "interest_payment_rule_period_cd": "1M",
                "interest_payment_rule_day_num": "31",
                "day_convention_cd": "ACT/365",
                "regular_payment_rule_start_dttm": ASSESSMENT_DATE,
                "regular_payment_rule_period_cd": "1M",
                "regular_payment_rule_day_num": "31",
                "accrued_interest": "0",
                "deal_report_dttm": ASSESSMENT_DATE,
            }
        )
        d = normalize(f, ident, data.name, True)
        with self._lock:
            self.items[ident] = d
            self.save()
        return d
