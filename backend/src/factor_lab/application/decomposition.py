"""Exact factor decomposition of a metric change between two report dates.

The calculator itself is the model: every coalition of factor groups is re-valued,
so non-linearity (min over scenarios, convexity) and interactions are captured
exactly and the contributions sum to the observed change (Shapley efficiency).

The portfolio lives in the calculator. Coalitions only differ by the report date the
deals are rolled to (with or without `portfolio_roll`) and by the curves, so all
coalitions of all date pairs that value deals on one date go out as a single request
with many curve sets: each deal's schedule is built once per date, not once per coalition.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from ..domain.curves import BASE, CURVE_BUCKETS, Curve, ScenarioCurves, blend
from ..domain.metrics import Valuation
from ..domain.shapley import exact_shapley
from .ports import CurveService, DealValues, PortfolioSession

ROLL = "portfolio_roll"
FACTORS = [ROLL, *CURVE_BUCKETS]
FACTOR_LABELS = {
    ROLL: "Течение времени и амортизация портфеля (погашения, новые фиксации ставки)",
    "curve_short": "Короткий участок кривой (до 1 года)",
    "curve_medium": "Средний участок кривой (1–5 лет)",
    "curve_long": "Длинный участок кривой (свыше 5 лет)",
}
COALITIONS = [frozenset(c) for k in range(len(FACTORS) + 1) for c in combinations(FACTORS, k)]
EMPTY, FULL = frozenset(), frozenset(FACTORS)
TOP_DEALS = 10


@dataclass
class PeriodState:
    report_date: str
    curves: ScenarioCurves
    valuation: Valuation
    count: int = 0
    sample: dict[str, DealValues] = field(default_factory=dict)


def _name(pair: int, coalition: frozenset) -> str:
    return f"{pair}:{'+'.join(sorted(coalition))}"


class Decomposer:
    def __init__(self, session: PortfolioSession, curve_service: CurveService, workers: int = 2):
        self.session = session
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

    def decompose_pairs(self, states: list[PeriodState], pairs: list[tuple[int, int]], metrics: list[str],
                        progress: Callable[[int, int], None] | None = None) -> list[dict[str, Any]]:
        """Decomposition of every (start, end) index pair, computed with one request per date."""
        jobs: dict[str, list[tuple[str, dict[str, Curve]]]] = defaultdict(list)
        for p, (a, b) in enumerate(pairs):
            start, end = states[a], states[b]
            for c in COALITIONS:
                if c in (EMPTY, FULL):
                    continue
                state = end if ROLL in c else start
                base = blend(start.curves.base, end.curves.base, {f for f in c if f != ROLL})
                jobs[state.report_date].append((_name(p, c), self._scenario_curves(start, end, base).all()))

        dates = list(jobs)
        totals: dict[str, dict[str, float]] = {}
        done = 0
        with ThreadPoolExecutor(max(1, self.workers)) as pool:
            for res in pool.map(lambda d: self.session.value(d, jobs[d]), dates):
                totals.update(res.totals)
                done += 1
                if progress:
                    progress(done, len(dates))

        movers = [self.session.movers(states[a].report_date, states[b].report_date, TOP_DEALS) for a, b in pairs]
        deal_values = self._mover_values(states, pairs, jobs, movers)

        out = []
        for p, (a, b) in enumerate(pairs):
            start, end = states[a], states[b]
            values: dict[frozenset, Valuation] = {EMPTY: start.valuation, FULL: end.valuation}
            for c in COALITIONS:
                if c not in values:
                    state = end if ROLL in c else start
                    values[c] = Valuation(state.report_date, state.report_date, totals[_name(p, c)])
            out.append(self._summarize(start, end, values, metrics, movers[p], deal_values[p]))
        return out

    def _mover_values(self, states, pairs, jobs, movers) -> list[dict[frozenset, dict[str, float]]]:
        """Base NPV of each pair's top movers under every coalition (0 where a deal is gone)."""
        ids_by_date: dict[str, set[str]] = defaultdict(set)
        for (a, b), top in zip(pairs, movers):
            ids = {m["deal_id"] for m in top}
            ids_by_date[states[a].report_date] |= ids
            ids_by_date[states[b].report_date] |= ids
        by_index = {s.report_date: s for s in states}
        per_date: dict[str, dict[str, dict[str, float]]] = {}  # date -> set name -> deal -> base npv
        for d, ids in ids_by_date.items():
            if not ids:
                continue
            sets = [("own", by_index[d].curves.all()), *jobs.get(d, [])]
            res = self.session.value(d, sets, only_ids=sorted(ids))
            table: dict[str, dict[str, float]] = defaultdict(dict)
            for deal_id, v in res.deals.items():
                for (name, _), npv in zip(sets, v.npv):
                    table[name][deal_id] = npv.get(BASE, 0.0)
            per_date[d] = table
        out = []
        for p, (a, b) in enumerate(pairs):
            start, end = states[a].report_date, states[b].report_date
            vals: dict[frozenset, dict[str, float]] = {
                EMPTY: per_date.get(start, {}).get("own", {}),
                FULL: per_date.get(end, {}).get("own", {}),
            }
            for c in COALITIONS:
                if c not in vals:
                    d = end if ROLL in c else start
                    vals[c] = per_date.get(d, {}).get(_name(p, c), {})
            out.append(vals)
        return out

    @staticmethod
    def _summarize(start: PeriodState, end: PeriodState, values: dict[frozenset, Valuation], metrics: list[str],
                   top: list[dict], deal_values: dict[frozenset, dict[str, float]]) -> dict[str, Any]:
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
                    ({"factors": [a, b], "value": v} for (a, b), v in inter.items()),
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
                    "+".join(sorted(s)) or "start": values[s].worst_scenario for s in COALITIONS
                }
        deals = []
        for m in top:
            d = m["deal_id"]
            phi, _, _ = exact_shapley(FACTORS, lambda s, d=d: deal_values[s].get(d, 0.0))
            deals.append({
                "deal_id": d,
                "before": m["before"],
                "after": m["after"],
                "change": m["change"],
                "contributions": {f: phi[f] for f in FACTORS},
            })
        out["deals"] = deals
        return out
