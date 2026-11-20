"""Run orchestration: deals → curves → calculator → decomposition → factor model → explanation."""

from __future__ import annotations

import logging
import traceback
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..domain.curves import BASE
from ..domain.portfolio import DealRecord, roll_forward
from .analytics.factor_model import PanelSpec, analyze_panel
from .analytics.panel import FIELD_LABELS, TARGETS, build_panel
from .decomposition import Decomposer, PeriodState
from .explanation import ExplainService
from .ports import Calculator, CurveService, DealSource, RunRepository

log = logging.getLogger(__name__)


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
                 repository: RunRepository, explainer: ExplainService, workers: int = 4):
        self.deals = deals
        self.curves = curves
        self.calculator = calculator
        self.repo = repository
        self.explainer = explainer
        self.workers = workers

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
        step("Загрузка сделок из источника", 0.02)
        source = [d for d in self.deals.load() if d.get("currency_id", "").upper() == req.currency]
        if not source:
            raise ValueError(f"в источнике нет сделок в валюте {req.currency}")
        report_dates = sorted({d["report_dttm"][:10] for d in source})
        if len(report_dates) != 1:
            raise ValueError("сделки источника должны иметь одну дату среза")
        report_date = report_dates[0]
        dates = self.periods(req, report_date)

        step("Запрос кривых и стресс-сценариев в curve-parser", 0.08)
        snapshots = {}
        for i, d in enumerate(dates):
            snapshots[d] = self.curves.snapshot(d)
            step(f"Кривая на {d}: торговый день {snapshots[d].base.trade_date}", 0.08 + 0.22 * (i + 1) / len(dates))

        step("Построение графиков платежей в калькуляторе", 0.32)
        base_run = self.calculator.value(source, snapshots[dates[0]].all(), report_date, include_cashflows=True)
        valid = [d for d in source if d["deal_id"] not in base_run.valuation.failed]
        excluded = [{"deal_id": k, "reason": v} for k, v in base_run.valuation.failed.items()]

        step("Оценка NPV портфеля по сценариям на каждую дату", 0.36)
        states: list[PeriodState] = []

        def value_period(d: str) -> PeriodState:
            rolled: list[DealRecord] = []
            for deal in valid:
                r = roll_forward(deal, base_run.schedules.get(deal["deal_id"], []), d)
                if r is not None:
                    rolled.append(r)
            v = self.calculator.value(rolled, snapshots[d].all(), d).valuation
            ok = [r for r in rolled if r["deal_id"] not in v.failed]
            return PeriodState(report_date=d, deals=ok, curves=snapshots[d], valuation=v)

        with ThreadPoolExecutor(self.workers) as pool:
            states = list(pool.map(value_period, dates))
        for s in states:
            for k, msg in s.valuation.failed.items():
                excluded.append({"deal_id": k, "reason": f"{s.report_date}: {msg}"})

        periods = []
        for s in states:
            v = s.valuation
            periods.append({
                "date": s.report_date,
                "curve_date": s.curves.base.trade_date,
                "deals": len(s.deals),
                "npv_base": v.npv_base,
                "ec": v.ec,
                "worst_scenario": v.worst_scenario,
                "npv": v.npv,
                "dnpv": v.dnpv(),
                "curve": [{"tenor_years": p.tenor_years, "rate": p.rate} for p in s.curves.base.points],
                "curve_features": s.curves.base.features(),
            })

        step("Точное разложение изменения ЭКпрбк и NPV (Шепли по калькулятору)", 0.5)
        decomposer = Decomposer(self.calculator, self.curves, self.workers)
        decomposition = decomposer.decompose(states[0], states[-1], ["ec", "npv_base"])
        steps = []
        for i in range(1, len(states)):
            step(f"Разложение {states[i - 1].report_date} → {states[i].report_date}", 0.5 + 0.2 * i / len(states))
            d = decomposer.decompose(states[i - 1], states[i], ["ec", "npv_base"])
            steps.append({
                "from": d["from"], "to": d["to"],
                **{m: {c["factor"]: c["shapley"] for c in d["metrics"][m]["contributions"]}
                   | {"change": d["metrics"][m]["change"], "residual": d["metrics"][m]["residual"]}
                   for m in ("ec", "npv_base")},
            })

        step("Факторная модель: нелинейность, взаимодействия, устойчивость", 0.72)
        panel = build_panel([(s.report_date, s.deals, s.valuation, s.curves.base.features()) for s in states])
        target = req.target if req.target in TARGETS or req.target.startswith("dnpv_") else "dnpv_worst_per_rub"
        factor_model = analyze_panel(panel, PanelSpec(target=target), FIELD_LABELS)

        reliability, limitations = self._reliability(decomposition, factor_model, steps, len(states))
        result: dict[str, Any] = {
            "request": req.to_dict(),
            "source": {"deals": self.deals.describe(), "report_date": report_date, "loaded": len(source),
                       "valued": len(valid), "excluded": excluded},
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
