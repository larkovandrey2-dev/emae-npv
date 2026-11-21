"""Calculator adapter: cashflow-rs HTTP API (axum)."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import httpx

from ..application.ports import CalculatorResult, CurveSets, DealValues, PortfolioValuation
from ..domain.curves import Curve
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord, ScheduledFlow


class CalculatorError(RuntimeError):
    pass


def _validate_valuation_response(data: object, scenarios: list[str]) -> dict:
    """Reject truncated or partial cashflow-rs responses before exposing results."""
    try:
        if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list) or not isinstance(data.get("deals"), list):
            raise ValueError("missing scenarios or deals")
        totals = {row["scenario"]: float(row["total"]["npv"]) for row in data["scenarios"]}
        if set(totals) != set(scenarios) or len(data["scenarios"]) != len(scenarios):
            raise ValueError("portfolio scenario totals are incomplete")
        if any(not math.isfinite(v) for v in totals.values()):
            raise ValueError("portfolio contains a non-finite NPV")
        for deal in data["deals"]:
            rows = deal.get("scenarios")
            if not isinstance(rows, list):
                raise ValueError("deal is missing scenario results")
            names = [row.get("scenario") for row in rows]
            if len(names) != len(scenarios) or set(names) != set(scenarios):
                raise ValueError(f"deal {deal.get('deal_id')} has incomplete scenario results")
            for row in rows:
                if not row.get("error"):
                    npv = float(row["summary"]["npv"])
                    if not math.isfinite(npv):
                        raise ValueError("deal contains a non-finite NPV")
        return data
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise CalculatorError(f"неполный ответ калькулятора: {exc}") from exc


def curves_payload(curves: dict[str, Curve]) -> list[dict]:
    return [
        {
            "scenario": name,
            "currency": "RUB",
            "compounding": "annual",
            "points": [
                {"tenor_days": max(1, round(p.tenor_years * 365)), "zero_rate_percent": p.rate * 100}
                for p in curve.points
            ],
        }
        for name, curve in curves.items()
    ]


class HttpPortfolioSession:
    """Portfolio held by cashflow-rs (`/api/v1/portfolios`): JSON batches in, totals out."""

    def __init__(self, client: httpx.Client, base_url: str, indicators: list[dict], retries: int = 2,
                 prepayment: dict | None = None):
        self.client = client
        self.base = base_url
        self.retries = retries
        body: dict = {"indicators": indicators}
        if prepayment is not None:
            body["prepayment"] = prepayment
        created = self._call("POST", "/api/v1/portfolios", body)
        self.id = created["id"]
        self.path = f"/api/v1/portfolios/{self.id}"

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        for attempt in range(self.retries + 1):
            try:
                r = self.client.request(method, f"{self.base}{path}", json=body, params=params)
                break
            except httpx.TransportError as exc:
                # A read timeout can happen after the calculator has already
                # accepted the upload or created a portfolio.
                retryable = method == "GET" or isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                if not retryable or attempt == self.retries:
                    raise CalculatorError(f"калькулятор недоступен: {exc}") from exc
        if r.status_code >= 400:
            raise CalculatorError(f"калькулятор ответил {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else None

    def upload(self, deals: list[DealRecord], currency: str) -> dict:
        return self._call("POST", f"{self.path}/deals", {"deals": deals, "currency": currency})

    def seal(self, dates: list[str]) -> dict:
        return self._call("POST", f"{self.path}/seal", {"dates": dates})

    def value(self, as_of: str, sets: CurveSets, sample_ids: list[str] | None = None,
              only_ids: list[str] | None = None, keep: str | None = None) -> PortfolioValuation:
        body: dict = {
            "as_of": as_of,
            "curve_sets": [{"name": name, "curves": curves_payload(c)} for name, c in sets],
            "scenarios": list(sets[0][1]),
            "sample_ids": [int(i) for i in sample_ids or []],
        }
        if only_ids is not None:
            body["only_ids"] = [int(i) for i in only_ids]
        if keep:
            body["keep"] = keep
        data = self._call("POST", f"{self.path}/valuations", body)
        self._validate_portfolio_valuation(data, as_of, sets, body["scenarios"], only_ids is not None)
        return PortfolioValuation(
            as_of=data["as_of"],
            valued=data["valued"],
            matured=data["matured"],
            failed_total=data["failed_total"],
            failures={str(f["deal_id"]): f["error"] for f in data["failures"]},
            totals={s["name"]: s["npv"] for s in data["sets"]},
            deals={str(d["deal_id"]): DealValues(d["outstanding"], d["npv"]) for d in data["deals"]},
        )

    @staticmethod
    def _validate_portfolio_valuation(data: dict, as_of: str, sets: CurveSets,
                                      scenarios: list[str], all_deal_sets: bool) -> None:
        def valid_npv(row: dict) -> bool:
            return set(row) == set(scenarios) and all(math.isfinite(value) for value in row.values())

        try:
            if data["as_of"] != as_of:
                raise ValueError("не совпадает дата оценки")
            if data["failed_total"]:
                raise ValueError(f"не оценено сделок: {data['failed_total']}")
            totals = data["sets"]
            if [row["name"] for row in totals] != [name for name, _ in sets]:
                raise ValueError("не совпадают наборы кривых")
            if any(not valid_npv(row["npv"]) for row in totals):
                raise ValueError("неполные или некорректные итоговые NPV")
            expected_rows = len(sets) if all_deal_sets else 1
            seen = set()
            for row in data["deals"]:
                ident = row["deal_id"]
                if ident in seen or len(row["npv"]) != expected_rows or not all(map(valid_npv, row["npv"])):
                    raise ValueError(f"некорректная оценка сделки {ident}")
                seen.add(ident)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CalculatorError(f"калькулятор вернул неполную оценку на {as_of}: {exc}") from exc

    def movers(self, from_key: str, to_key: str, n: int) -> list[dict]:
        rows = self._call("GET", f"{self.path}/movers", params={"from": from_key, "to": to_key, "n": n})
        return [{**r, "deal_id": str(r["deal_id"])} for r in rows]

    def close(self) -> None:
        try:
            self.client.delete(f"{self.base}{self.path}", timeout=10)
        except httpx.HTTPError:
            pass


class CashflowHttpCalculator:
    def __init__(self, base_url: str, timeout_s: float = 900, indicators_csv: str | None = None):
        self.base_url = base_url.rstrip("/")
        # several upload batches go out at once; keep enough pooled connections for them
        self.client = httpx.Client(timeout=timeout_s, limits=httpx.Limits(max_connections=16))
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

    def open_portfolio(self, prepayment: dict | None = None) -> HttpPortfolioSession:
        return HttpPortfolioSession(self.client, self.base_url, self.indicators, prepayment=prepayment)

    def prepayment_defaults(self) -> dict:
        r = self.client.get(f"{self.base_url}/api/v1/prepayment", timeout=5)
        r.raise_for_status()
        return r.json()

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
        data = _validate_valuation_response(r.json(), scenarios)
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
