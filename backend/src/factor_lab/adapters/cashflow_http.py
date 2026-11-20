"""Calculator adapter: cashflow-rs HTTP API (axum)."""

from __future__ import annotations

import csv
from pathlib import Path

import httpx

from ..application.ports import CalculatorResult
from ..domain.curves import Curve
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord, ScheduledFlow


class CalculatorError(RuntimeError):
    pass


class CashflowHttpCalculator:
    def __init__(self, base_url: str, timeout_s: float = 120, indicators_csv: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout_s)
        self.indicators = self._load_indicators(indicators_csv) if indicators_csv else []

    @staticmethod
    def _load_indicators(path: str) -> list[dict]:
        """Optional `code;effective_date;rate_percent` file merged over the server defaults."""
        series: dict[str, list[dict]] = {}
        p = Path(path)
        if not p.is_file():
            return []
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter=";"):
                series.setdefault(row["code"], []).append(
                    {"effective_date": row["effective_date"], "rate_percent": float(row["rate_percent"])})
        return [{"code": c, "points": pts} for c, pts in series.items()]

    def health(self) -> bool:
        try:
            return self.client.get(f"{self.base_url}/api/v1/health", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def value(self, deals: list[DealRecord], curves: dict[str, Curve], report_date: str,
              include_cashflows: bool = False) -> CalculatorResult:
        if not deals:
            return CalculatorResult(Valuation(report_date, "", {s: 0.0 for s in curves}, {}, {}))
        scenarios = list(curves)
        body = {
            "deals": deals,
            "curves": [
                {
                    "scenario": name,
                    "curve_date": report_date,
                    "currency": "RUB",
                    "compounding": "annual",
                    "points": [
                        {"tenor_days": max(1, round(p.tenor_years * 365)), "zero_rate_percent": p.rate * 100}
                        for p in curve.points
                    ],
                }
                for name, curve in curves.items()
            ],
            "scenarios": scenarios,
            "indicators": self.indicators,
            "include_cashflows": include_cashflows,
        }
        try:
            r = self.client.post(f"{self.base_url}/api/v1/valuations", json=body)
        except httpx.HTTPError as exc:
            raise CalculatorError(f"калькулятор недоступен: {exc}") from exc
        if r.status_code != 200:
            raise CalculatorError(f"калькулятор ответил {r.status_code}: {r.text[:500]}")
        data = r.json()
        npv = {s["scenario"]: s["total"]["npv"] for s in data["scenarios"]}
        deal_npv: dict[str, dict[str, float]] = {}
        failed: dict[str, str] = {}
        schedules: dict[str, list[ScheduledFlow]] = {}
        for d in data["deals"]:
            ident = str(d["deal_id"])
            errors = [s["error"] for s in d["scenarios"] if s.get("error")]
            if errors:
                failed[ident] = errors[0]
                continue
            deal_npv[ident] = {s["scenario"]: s["summary"]["npv"] for s in d["scenarios"]}
            if include_cashflows:
                flows = d["scenarios"][0].get("cashflows") or []
                schedules[ident] = [ScheduledFlow(c["date"], c["principal"], c["balance_after"]) for c in flows]
        if failed:
            # Portfolio totals must cover the same deals in every scenario.
            npv = {s: sum(v[s] for v in deal_npv.values()) for s in scenarios}
        valuation = Valuation(report_date=report_date, curve_date=report_date, npv=npv,
                              deal_npv=deal_npv, failed=failed)
        return CalculatorResult(valuation, schedules)
