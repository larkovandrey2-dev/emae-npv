"""Exact factor decomposition of a metric change between two report dates.

The calculator itself is the model: every coalition of factor groups is re-valued,
so non-linearity (min over scenarios, convexity) and interactions are captured
exactly and the contributions sum to the observed change (Shapley efficiency).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import combinations
from typing import Any

from ..domain.curves import BASE, CURVE_BUCKETS, Curve, ScenarioCurves, blend
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord
from ..domain.shapley import exact_shapley
from .ports import Calculator, CurveService

ROLL = "portfolio_roll"
FACTORS = [ROLL, *CURVE_BUCKETS]
FACTOR_LABELS = {
    ROLL: "Течение времени и амортизация портфеля (погашения, новые фиксации ставки)",
    "curve_short": "Короткий участок кривой (до 1 года)",
    "curve_medium": "Средний участок кривой (1–5 лет)",
    "curve_long": "Длинный участок кривой (свыше 5 лет)",
}


@dataclass
class PeriodState:
    report_date: str
    deals: list[DealRecord]
    curves: ScenarioCurves
    valuation: Valuation


class Decomposer:
    def __init__(self, calculator: Calculator, curve_service: CurveService, workers: int = 4):
        self.calculator = calculator
        self.curves = curve_service
        self.workers = workers
        self._shock_cache: dict[Any, ScenarioCurves] = {}

    def _scenario_curves(self, start: PeriodState, end: PeriodState, base: Curve) -> ScenarioCurves:
        if base.key() == start.curves.base.key():
            return start.curves
        if base.key() == end.curves.base.key():
            return end.curves
        key = base.key()
        if key not in self._shock_cache:
            self._shock_cache[key] = self.curves.shock(base)
        return self._shock_cache[key]

    def _value(self, start: PeriodState, end: PeriodState, coalition: frozenset) -> Valuation:
        if coalition == frozenset():
            return start.valuation
        if coalition == frozenset(FACTORS):
            return end.valuation
        state = end if ROLL in coalition else start
        moved = {f for f in coalition if f != ROLL}
        base = blend(start.curves.base, end.curves.base, moved)
        curves = self._scenario_curves(start, end, base).all()
        return self.calculator.value(state.deals, curves, state.report_date).valuation

    def decompose(self, start: PeriodState, end: PeriodState, metrics: list[str]) -> dict[str, Any]:
        coalitions = [frozenset(c) for k in range(len(FACTORS) + 1) for c in combinations(FACTORS, k)]
        with ThreadPoolExecutor(self.workers) as pool:
            values = dict(zip(coalitions, pool.map(lambda c: self._value(start, end, c), coalitions)))

        out: dict[str, Any] = {
            "from": start.report_date,
            "to": end.report_date,
            "factors": [{"id": f, "label": FACTOR_LABELS[f]} for f in FACTORS],
            "metrics": {},
            "evaluations": len(values),
        }
        for metric in metrics:
            phi, inter, _ = exact_shapley(FACTORS, lambda s, m=metric: values[s].metric(m))
            before = start.valuation.metric(metric)
            after = end.valuation.metric(metric)
            change = after - before
            explained = sum(phi.values())
            oat = {f: values[frozenset({f})].metric(metric) - before for f in FACTORS}
            oat_sum = sum(oat.values())
            out["metrics"][metric] = {
                "before": before,
                "after": after,
                "change": change,
                "contributions": [
                    {"factor": f, "shapley": phi[f], "one_at_a_time": oat[f]} for f in FACTORS
                ],
                "interactions": sorted(
                    (
                        {"factors": [a, b], "value": v}
                        for (a, b), v in inter.items()
                    ),
                    key=lambda x: -abs(x["value"]),
                ),
                "explained": explained,
                "residual": change - explained,
                "explained_share": explained / change if abs(change) > 1e-9 else 1.0,
                "linear_baseline": {
                    "sum": oat_sum,
                    "residual": change - oat_sum,
                    "unexplained_share": (change - oat_sum) / change if abs(change) > 1e-9 else 0.0,
                },
            }
            if metric == "ec":
                out["metrics"][metric]["worst_scenario_by_coalition"] = {
                    "+".join(sorted(s)) or "start": values[s].worst_scenario for s in coalitions
                }
        out["deals"] = self._deal_contributions(start, end, values)
        return out

    @staticmethod
    def _deal_contributions(start: PeriodState, end: PeriodState, values: dict) -> list[dict]:
        ids = set(start.valuation.deal_npv) | set(end.valuation.deal_npv)

        def npv(v: Valuation, d: str) -> float:
            return v.deal_npv.get(d, {}).get(BASE, 0.0)

        rows = []
        for d in ids:
            change = npv(end.valuation, d) - npv(start.valuation, d)
            rows.append((abs(change), d, change))
        rows.sort(reverse=True)
        out = []
        for _, d, change in rows[:10]:
            phi, _, _ = exact_shapley(FACTORS, lambda s, d=d: npv(values[s], d))
            out.append(
                {
                    "deal_id": d,
                    "before": npv(start.valuation, d),
                    "after": npv(end.valuation, d),
                    "change": change,
                    "contributions": {f: phi[f] for f in FACTORS},
                }
            )
        return out
