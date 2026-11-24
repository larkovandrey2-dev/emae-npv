"""Run orchestration: deals → curves → calculator → decomposition → factor model → explanation."""

from __future__ import annotations

import logging
import random
import traceback
from calendar import monthrange
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..domain.curves import BASE
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord
from .analytics.factor_model import PanelSpec, analyze_panel
from .analytics.panel import FIELD_LABELS, TARGETS, build_panel
from .decomposition import Decomposer, PeriodState
from .explanation import ExplainService
from .ports import Calculator, CurveService, DealSource, PortfolioSession, RunRepository

log = logging.getLogger(__name__)

UPLOAD_BATCH = 25_000  # deals per JSON request to the calculator (~15 MB)
UPLOAD_PARALLEL = 4  # upload requests in flight at once
MAX_EXCLUDED = 200  # excluded deals listed in the result; the rest are only counted


def _n(value: int) -> str:
    """Thousands separated by spaces: 2 474 921."""
    return f"{value:,}".replace(",", " ")


@dataclass
class Upload:
    rows: int = 0
    matched: int = 0
    accepted: int = 0
    rejected_total: int = 0
    rejected: list[dict[str, str]] = field(default_factory=list)
    report_dates: set[str] = field(default_factory=set)
    sample_seen: list[DealRecord] = field(default_factory=list)
    sample: dict[str, DealRecord] = field(default_factory=dict)


@dataclass
class RunRequest:
    start: str | None = None
    end: str | None = None
    frequency: str = "month"  # month | quarter
    dates: list[str] = field(default_factory=list)
    target: str = "dnpv_worst_per_rub"
    explain: bool = True
    currency: str = "RUB"
    # generated portfolio to run on (None: the configured deal source)
    dataset: str | None = None
    # behavioural prepayment in the calculator; rates override the standard CPR per model code
    prepayment: bool = True
    prepayment_rates: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def month_ends(start: str, end: str, step: int) -> list[str]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    if e < s:
        raise ValueError("end is before start")
    out = [s.isoformat()]
    y, m = s.year, s.month
    while True:
        m += step
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        d = date(y, m, monthrange(y, m)[1])
        if d > e:
            break
        out.append(d.isoformat())
    if out[-1] != e.isoformat() and e > s:
        out.append(e.isoformat())
    return out


class RunPipeline:
    def __init__(self, deals: DealSource, curves: CurveService, calculator: Calculator,
                 repository: RunRepository, explainer: ExplainService, workers: int = 2,
                 panel_sample: int = 5000):
        self.deals = deals
        self.curves = curves
        self.calculator = calculator
        self.repo = repository
        self.explainer = explainer
        self.workers = workers
        self.panel_sample = panel_sample

    def periods(self, req: RunRequest, report_date: str) -> list[str]:
        if req.dates:
            dates = sorted(set(req.dates))
        else:
            start = req.start or report_date
            end = req.end or f"{int(start[:4]) + 1}{start[4:]}".replace("-02-29", "-02-28")
            dates = month_ends(start, end, 3 if req.frequency == "quarter" else 1)
        if len(dates) < 2:
            raise ValueError("at least two report dates are required")
        if len(dates) > 40:
            raise ValueError("at most 40 report dates per run")
        if dates[0] < report_date:
            raise ValueError(f"dates must not be earlier than the deals report date {report_date}")
        return dates

    def run(self, run_id: str, req: RunRequest, source: DealSource | None = None) -> None:
        try:
            result = self._run(run_id, req, source or self.deals)
            self.repo.complete(run_id, result)
        except Exception as exc:  # the run record must always end in a terminal state
            log.error("run_failed id=%s: %s\n%s", run_id, exc, traceback.format_exc())
            self.repo.fail(run_id, str(exc))

    def _run(self, run_id: str, req: RunRequest, source: DealSource) -> dict[str, Any]:
        step = lambda stage, p: self.repo.progress(run_id, stage, p)  # noqa: E731
        prepayment = {"enabled": req.prepayment, "rates": dict(req.prepayment_rates)}
        session = self.calculator.open_portfolio(prepayment)
        try:
            return self._run_in(session, run_id, req, step, source)
        finally:
            session.close()

    def _upload(self, session: PortfolioSession, currency: str, step, source: DealSource) -> Upload:
        """Streams the source into the calculator in JSON batches, several in flight at once.

        On the way it collects the report dates and a reservoir sample of deals for the
        factor model, so the whole portfolio is never held here.
        """
        up = Upload()
        rng = random.Random(17)
        in_flight: deque[Future] = deque()

        def settle(f: Future) -> None:
            r = f.result()
            up.accepted += r["accepted"]
            up.rejected_total += r["rejected_total"]
            for x in r["rejected"]:
                if len(up.rejected) < MAX_EXCLUDED:
                    up.rejected.append({"deal_id": str(x.get("deal_id", "")), "reason": x["error"]})

        with ThreadPoolExecutor(UPLOAD_PARALLEL) as pool:
            for batch in source.batches(UPLOAD_BATCH):
                mine = []
                for d in batch:
                    up.rows += 1
                    if d.get("currency_id", "").upper() != currency:
                        continue
                    mine.append(d)
                    up.report_dates.add(d.get("report_dttm", "")[:10])
                    seen = len(up.sample_seen)
                    if seen < self.panel_sample:
                        up.sample_seen.append(d)
                    else:
                        j = rng.randrange(up.matched + 1)
                        if j < self.panel_sample:
                            up.sample_seen[j] = d
                    up.matched += 1
                if not mine:
                    continue
                in_flight.append(pool.submit(session.upload, mine, currency))
                while len(in_flight) >= UPLOAD_PARALLEL * 2:
                    settle(in_flight.popleft())
                step(f"Loading deals: {_n(up.matched)} of {_n(up.rows)}",
                     0.02 + min(0.08, 0.08 * up.rows / max(up.rows + 1, 2_500_000)))
            while in_flight:
                settle(in_flight.popleft())
        up.sample = {d["deal_id"]: d for d in up.sample_seen}
        return up

    def _run_in(self, session: PortfolioSession, run_id: str, req: RunRequest, step,
                source: DealSource) -> dict[str, Any]:
        step("Loading deals", 0.02)
        up = self._upload(session, req.currency, step, source)
        if up.matched == 0:
            raise ValueError(f"no {req.currency} deals in the source")
        if len(up.report_dates) != 1:
            raise ValueError("source deals must share one report date")
        report_date = next(iter(up.report_dates))
        dates = self.periods(req, report_date)

        step("Fetching curves", 0.1)
        snapshots = {}
        for i, d in enumerate(dates):
            snapshots[d] = self.curves.snapshot(d)
            step(f"Curve {d}: trade date {snapshots[d].base.trade_date}", 0.1 + 0.15 * (i + 1) / len(dates))

        step(f"Cash flows: {_n(up.accepted)} deals × {len(dates)} dates", 0.26)
        sealed = session.seal(dates)
        excluded = list(up.rejected)
        excluded_total = up.rejected_total + sealed["failed_total"]
        for f in sealed["failures"]:
            if len(excluded) < MAX_EXCLUDED:
                excluded.append({"deal_id": str(f["deal_id"]), "reason": f["error"]})

        sample_ids = list(up.sample)
        states: list[PeriodState] = []
        for i, d in enumerate(dates):
            v = session.value(d, [("own", snapshots[d].all())], sample_ids=sample_ids, keep=d)
            valuation = Valuation(report_date=d, curve_date=d, npv=v.totals["own"],
                                  deal_npv={k: x.npv[0] for k, x in v.deals.items()}, failed=v.failures)
            states.append(PeriodState(d, snapshots[d], valuation, count=v.valued, sample=v.deals))
            excluded_total += v.failed_total
            for k, msg in v.failures.items():
                if len(excluded) < MAX_EXCLUDED:
                    excluded.append({"deal_id": k, "reason": f"{d}: {msg}"})
            step(f"NPV {d}: {_n(v.valued)} deals × 7 scenarios", 0.3 + 0.18 * (i + 1) / len(dates))

        periods = []
        for s in states:
            v = s.valuation
            periods.append({
                "date": s.report_date,
                "curve_date": s.curves.base.trade_date,
                "deals": s.count,
                "npv_base": v.npv_base,
                "ec": v.ec,
                "worst_scenario": v.worst_scenario,
                "npv": v.npv,
                "dnpv": v.dnpv(),
                "curve": [{"tenor_years": p.tenor_years, "rate": p.rate} for p in s.curves.base.points],
                "curve_features": s.curves.base.features(),
            })

        step("Shapley attribution", 0.5)
        last = len(states) - 1
        pairs = [(0, last)] + [(i - 1, i) for i in range(1, len(states)) if (i - 1, i) != (0, last)]
        decomposer = Decomposer(session, self.curves, self.workers)
        results = decomposer.decompose_pairs(
            states, pairs, ["ec", "npv_base"],
            progress=lambda k, n: step(f"Attribution: {k} of {n} dates", 0.5 + 0.2 * k / n),
        )
        by_pair = dict(zip(pairs, results))
        decomposition = by_pair[(0, last)]
        steps = []
        for i in range(1, len(states)):
            d = by_pair[(i - 1, i)]
            steps.append({
                "from": d["from"], "to": d["to"],
                **{m: {c["factor"]: c["shapley"] for c in d["metrics"][m]["contributions"]}
                   | {"change": d["metrics"][m]["change"], "residual": d["metrics"][m]["residual"]}
                   for m in ("ec", "npv_base")},
            })

        step(f"Factor model on {_n(len(up.sample))} deals", 0.72)
        panel = build_panel([(s.report_date, self._sample_deals(up.sample, s), s.valuation, s.curves.base.features())
                             for s in states])
        target = req.target if req.target in TARGETS or req.target.startswith("dnpv_") else "dnpv_worst_per_rub"
        factor_model = analyze_panel(panel, PanelSpec(target=target), FIELD_LABELS)
        factor_model["sample"] = {"deals": len(up.sample), "of": up.accepted}

        behaviour = sealed.get("behaviour") or {}
        reliability, limitations = self._reliability(decomposition, factor_model, steps, len(states), behaviour)
        if len(up.sample) < up.accepted:
            limitations.append(
                f"The factor model is trained on a random sample of {_n(len(up.sample))} of {_n(up.accepted)} deals; "
                "totals, attribution and top deals use the full book.")
        result: dict[str, Any] = {
            "request": req.to_dict(),
            "source": {"deals": source.describe(), "dataset": req.dataset, "report_date": report_date,
                       "loaded": up.accepted, "valued": sealed["valid"], "excluded": excluded,
                       "excluded_total": excluded_total},
            "behaviour": behaviour,
            "periods": periods,
            "decomposition": decomposition,
            "steps": steps,
            "factor_model": factor_model,
            "target": {"id": target, "label": TARGETS.get(target, target)},
            "reliability": reliability,
            "limitations": limitations,
            "scenarios": [s for s in periods[0]["npv"] if s != BASE],
        }
        if req.explain:
            step("LLM summary", 0.9)
            try:
                result["explanation"] = self.explainer.explain(run_id, result, None, persist=False)
            except Exception as exc:
                result["explanation"] = {"error": str(exc)}
        step("Done", 1.0)
        return result

    @staticmethod
    def _sample_deals(sample: dict[str, DealRecord], state: PeriodState) -> list[DealRecord]:
        """Sampled deals as they stand on the period date (balance from the calculator's roll)."""
        out = []
        for deal_id, v in state.sample.items():
            base = sample.get(deal_id)
            if base is None:
                continue
            row = dict(base)
            row["report_dttm"] = state.report_date
            if row.get("deal_report_dttm"):
                row["deal_report_dttm"] = state.report_date
            row["current_principal_amt"] = f"{v.outstanding:.2f}"
            row.pop("accrued_interest", None)
            out.append(row)
        return out

    @staticmethod
    def _reliability(dec: dict, model: dict, steps: list, n_periods: int,
                     behaviour: dict | None = None) -> tuple[dict, list[str]]:
        reasons, limitations = [], []
        ec = dec["metrics"]["ec"]
        if abs(ec["change"]) > 0 and abs(ec["residual"]) > 0.01 * abs(ec["change"]):
            reasons.append("EC attribution does not add up to the observed change")
        signs = {}
        for s in steps:
            for f, v in s["ec"].items():
                if f not in ("change", "residual") and abs(v) > 0:
                    signs.setdefault(f, set()).add(v > 0)
        flip = [f for f, s in signs.items() if len(s) > 1]
        if flip:
            limitations.append("Contribution sign flips between adjacent dates for: " + ", ".join(flip))
        verdict = model.get("reliability", {}).get("verdict", "insufficient")
        if verdict != "reliable":
            reasons.extend(model.get("reliability", {}).get("reasons", []))
        limitations += [
            "Factor contributions are exact for each pair of dates (the calculator reprices them); "
            "global importance and response shapes are surrogate-model estimates.",
        ]
        prepay = (behaviour or {}).get("prepayment") or {}
        if prepay.get("enabled") and prepay.get("deals"):
            deals = _n(prepay["deals"])
            limitations.append(
                f"Prepayment applied to {deals} deals with a behaviour model: a constant annual CPR per model code, "
                "the same in every scenario; new business is not modelled.")
            if prepay.get("unknown_models"):
                limitations.append("Model codes without a CPR (deals follow the contract schedule): "
                                   + ", ".join(sorted(prepay["unknown_models"])))
        else:
            limitations.append("Deals follow the contract schedule: no new business, "
                               "no prepayment.")
        if n_periods < 6:
            limitations.append("Fewer than six dates: stability over time is preliminary.")
        if ec["linear_baseline"]["unexplained_share"] and abs(ec["linear_baseline"]["unexplained_share"]) > 0.1:
            limitations.append("Isolated effects do not add up to the EC change: nonlinearity "
                               "(scenario minimum, convexity) or factor interactions.")
        level = "high" if not reasons and not flip else ("medium" if len(reasons) <= 1 else "low")
        return {"confidence": level, "reasons": reasons, "factor_model": verdict}, limitations
