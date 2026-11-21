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
            raise ValueError("нужно минимум два отчётных периода")
        if len(dates) > 40:
            raise ValueError("не больше 40 отчётных периодов за прогон")
        if dates[0] < report_date:
            raise ValueError(f"периоды должны начинаться не раньше даты среза сделок {report_date}")
        return dates

    def run(self, run_id: str, req: RunRequest) -> None:
        try:
            result = self._run(run_id, req)
            self.repo.complete(run_id, result)
        except Exception as exc:  # the run record must always end in a terminal state
            log.error("run_failed id=%s: %s\n%s", run_id, exc, traceback.format_exc())
            self.repo.fail(run_id, str(exc))

    def _run(self, run_id: str, req: RunRequest) -> dict[str, Any]:
        step = lambda stage, p: self.repo.progress(run_id, stage, p)  # noqa: E731
        session = self.calculator.open_portfolio()
        try:
            return self._run_in(session, run_id, req, step)
        finally:
            session.close()

    def _upload(self, session: PortfolioSession, currency: str, step) -> Upload:
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
            for batch in self.deals.batches(UPLOAD_BATCH):
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
                step(f"Загрузка сделок в калькулятор: {up.matched:,} из прочитанных {up.rows:,}".replace(",", " "),
                     0.02 + min(0.08, 0.08 * up.rows / max(up.rows + 1, 2_500_000)))
            while in_flight:
                settle(in_flight.popleft())
        up.sample = {d["deal_id"]: d for d in up.sample_seen}
        return up

    def _run_in(self, session: PortfolioSession, run_id: str, req: RunRequest, step) -> dict[str, Any]:
        step("Загрузка сделок в калькулятор", 0.02)
        up = self._upload(session, req.currency, step)
        if up.matched == 0:
            raise ValueError(f"в источнике нет сделок в валюте {req.currency}")
        if len(up.report_dates) != 1:
            raise ValueError("сделки источника должны иметь одну дату среза")
        report_date = next(iter(up.report_dates))
        dates = self.periods(req, report_date)

        step("Запрос кривых и стресс-сценариев в curve-parser", 0.1)
        snapshots = {}
        for i, d in enumerate(dates):
            snapshots[d] = self.curves.snapshot(d)
            step(f"Кривая на {d}: торговый день {snapshots[d].base.trade_date}", 0.1 + 0.15 * (i + 1) / len(dates))

        step(f"Графики платежей и остатки {up.accepted:,} сделок на {len(dates)} дат".replace(",", " "), 0.26)
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
            step(f"NPV на {d}: {v.valued:,} сделок, 7 сценариев".replace(",", " "), 0.3 + 0.18 * (i + 1) / len(dates))

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

        step("Точное разложение изменения ЭКпрбк и NPV (Шепли по калькулятору)", 0.5)
        last = len(states) - 1
        pairs = [(0, last)] + [(i - 1, i) for i in range(1, len(states)) if (i - 1, i) != (0, last)]
        decomposer = Decomposer(session, self.curves, self.workers)
        results = decomposer.decompose_pairs(
            states, pairs, ["ec", "npv_base"],
            progress=lambda k, n: step(f"Разложение: коалиции на {k} из {n} дат", 0.5 + 0.2 * k / n),
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

        step(f"Факторная модель на выборке {len(up.sample):,} сделок".replace(",", " "), 0.72)
        panel = build_panel([(s.report_date, self._sample_deals(up.sample, s), s.valuation, s.curves.base.features())
                             for s in states])
        target = req.target if req.target in TARGETS or req.target.startswith("dnpv_") else "dnpv_worst_per_rub"
        factor_model = analyze_panel(panel, PanelSpec(target=target), FIELD_LABELS)
        factor_model["sample"] = {"deals": len(up.sample), "of": up.accepted}

        reliability, limitations = self._reliability(decomposition, factor_model, steps, len(states))
        if len(up.sample) < up.accepted:
            limitations.append(
                f"Факторная модель обучена на случайной выборке {len(up.sample):,} сделок из {up.accepted:,}; "
                "итоги, разложение и топ сделок посчитаны по всему портфелю.".replace(",", " "))
        result: dict[str, Any] = {
            "request": req.to_dict(),
            "source": {"deals": self.deals.describe(), "report_date": report_date, "loaded": up.accepted,
                       "valued": sealed["valid"], "excluded": excluded, "excluded_total": excluded_total},
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
            step("Объяснение результатов (LLM)", 0.9)
            try:
                result["explanation"] = self.explainer.explain(run_id, result, None, persist=False)
            except Exception as exc:
                result["explanation"] = {"error": str(exc)}
        step("Готово", 1.0)
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
    def _reliability(dec: dict, model: dict, steps: list, n_periods: int) -> tuple[dict, list[str]]:
        reasons, limitations = [], []
        ec = dec["metrics"]["ec"]
        if abs(ec["change"]) > 0 and abs(ec["residual"]) > 0.01 * abs(ec["change"]):
            reasons.append("разложение ЭКпрбк не сходится с наблюдаемым изменением")
        signs = {}
        for s in steps:
            for f, v in s["ec"].items():
                if f not in ("change", "residual") and abs(v) > 0:
                    signs.setdefault(f, set()).add(v > 0)
        flip = [f for f, s in signs.items() if len(s) > 1]
        if flip:
            limitations.append("знак вклада меняется между соседними периодами для: " + ", ".join(flip))
        verdict = model.get("reliability", {}).get("verdict", "insufficient")
        if verdict != "reliable":
            reasons.extend(model.get("reliability", {}).get("reasons", []))
        limitations += [
            "Вклады групп факторов точны для пары дат (разложение пересчитывает калькулятор), "
            "но глобальная важность и формы зависимостей — оценка суррогатной модели.",
            "Сделки двигаются во времени по контрактному графику без новых выдач и досрочных погашений.",
            "Поведенческие модели (prepayment) калькулятором не применяются.",
        ]
        if n_periods < 6:
            limitations.append("Периодов меньше шести — выводы об устойчивости во времени предварительные.")
        if ec["linear_baseline"]["unexplained_share"] and abs(ec["linear_baseline"]["unexplained_share"]) > 0.1:
            limitations.append("Изолированные эффекты не складываются в изменение ЭКпрбк: заметна нелинейность "
                               "(минимум по сценариям, выпуклость) или взаимодействие факторов.")
        level = "high" if not reasons and not flip else ("medium" if len(reasons) <= 1 else "low")
        return {"confidence": level, "reasons": reasons, "factor_model": verdict}, limitations
