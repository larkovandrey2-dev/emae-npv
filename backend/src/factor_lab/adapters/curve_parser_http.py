"""Curve service adapter: curve-parser HTTP API (MOEX G-curve + IRRBB shocks)."""

from __future__ import annotations

import math
import threading
from datetime import date, timedelta

import httpx

from ..domain.curves import Curve, CurvePoint, ScenarioCurves

LOOKBACK_DAYS = 10
REQUIRED_SCENARIOS = {"PARALLEL_UP", "PARALLEL_DOWN", "SHORT_UP", "SHORT_DOWN", "STEEPENER", "FLATTENER"}


class CurveServiceError(RuntimeError):
    pass


def _scenario_name(raw: str) -> str:
    return raw.upper()


class CurveParserClient:
    def __init__(self, base_url: str, timeout_s: float = 180):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout_s)
        self._cache: dict[str, ScenarioCurves] = {}
        self._lock = threading.Lock()

    def health(self) -> bool:
        try:
            return self.client.get(f"{self.base_url}/api/v1/ready", timeout=3).status_code == 200
        except httpx.HTTPError:
            return False

    def _get(self, path: str, **params):
        try:
            r = self.client.get(f"{self.base_url}{path}", params=params or None)
        except httpx.HTTPError as exc:
            raise CurveServiceError(f"curve-parser недоступен: {exc}") from exc
        return r

    def dates(self, start: str, end: str) -> list[str]:
        r = self._get("/api/v1/curves", **{"from": start, "to": end})
        if r.status_code != 200:
            raise CurveServiceError(f"curve-parser /curves: {r.status_code} {r.text[:300]}")
        return r.json()["dates"]

    def ingest(self, start: str, end: str) -> dict:
        try:
            r = self.client.post(f"{self.base_url}/api/v1/curves/ingest",
                                 json={"from": start, "to": end, "refresh": False})
        except httpx.HTTPError as exc:
            raise CurveServiceError(f"curve-parser ingest: {exc}") from exc
        if r.status_code != 200:
            raise CurveServiceError(f"curve-parser ingest: {r.status_code} {r.text[:300]}")
        return r.json()

    @staticmethod
    def _parse(payload: dict) -> ScenarioCurves:
        try:
            trade_date = date.fromisoformat(payload["date"])
            tenors = payload["tenors"]
            if not isinstance(tenors, list) or not tenors:
                raise ValueError("tenor grid is empty")
            tenors = sorted(tenors, key=lambda t: float(t["tenor_years"]))
            raw_grid = [float(t["tenor_years"]) for t in tenors]
            if any(not math.isfinite(x) or x <= 0 for x in raw_grid) or len(set(raw_grid)) != len(raw_grid):
                raise ValueError("tenor grid must contain distinct positive values")
            if len({max(1, round(t * 365)) for t in raw_grid}) != len(raw_grid):
                raise ValueError("tenors collide after conversion to days")
            base_points = tuple(CurvePoint(float(t["tenor_years"]), float(t["base_rate"])) for t in tenors)
            if any(not math.isfinite(p.rate) for p in base_points):
                raise ValueError("base curve contains a non-finite rate")
            base = Curve(trade_date.isoformat(), base_points)
            shocked: dict[str, list[CurvePoint]] = {}
            for t in tenors:
                names = set()
                for s in t["scenarios"]:
                    name = _scenario_name(s["scenario"])
                    if name in names:
                        raise ValueError(f"duplicate scenario {name} at tenor {t['tenor_years']}")
                    names.add(name)
                    rate = float(s["rate"])
                    if not math.isfinite(rate):
                        raise ValueError(f"scenario {name} contains a non-finite rate")
                    shocked.setdefault(name, []).append(CurvePoint(float(t["tenor_years"]), rate))
                if names != REQUIRED_SCENARIOS:
                    raise ValueError(f"expected six scenarios at each tenor; got {sorted(names)}")
            if set(shocked) != REQUIRED_SCENARIOS:
                raise ValueError(f"expected six consistent stress scenarios; got {sorted(shocked)}")
            return ScenarioCurves(base, {k: Curve(base.trade_date, tuple(v)) for k, v in shocked.items()})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CurveServiceError(f"invalid curve-parser payload: {exc}") from exc

    def snapshot(self, on_or_before: str) -> ScenarioCurves:
        with self._lock:
            if on_or_before in self._cache:
                return self._cache[on_or_before]
        end = date.fromisoformat(on_or_before)
        start = (end - timedelta(days=LOOKBACK_DAYS)).isoformat()
        found = self.dates(start, on_or_before)
        if not found:
            self.ingest(start, on_or_before)
            found = self.dates(start, on_or_before)
        if not found:
            raise CurveServiceError(f"нет кривой MOEX за {start}…{on_or_before}")
        trade = max(found)
        try:
            if not date.fromisoformat(start) <= date.fromisoformat(trade) <= end:
                raise ValueError
        except ValueError as exc:
            raise CurveServiceError(f"curve-parser вернул неверную дату сделки: {trade}") from exc
        r = self._get(f"/api/v1/curves/{trade}")
        if r.status_code != 200:
            raise CurveServiceError(f"curve-parser /curves/{trade}: {r.status_code} {r.text[:300]}")
        curves = self._parse(r.json())
        if curves.base.trade_date != trade:
            raise CurveServiceError(f"curve-parser date {curves.base.trade_date} не совпадает с запрошенной {trade}")
        with self._lock:
            self._cache[on_or_before] = curves
        return curves

    def shock(self, base: Curve) -> ScenarioCurves:
        body = {"tenors": [{"tenor_years": p.tenor_years, "base_rate": p.rate} for p in base.points]}
        try:
            r = self.client.post(f"{self.base_url}/api/v1/curves/shock", json=body)
        except httpx.HTTPError as exc:
            raise CurveServiceError(f"curve-parser shock: {exc}") from exc
        if r.status_code != 200:
            raise CurveServiceError(f"curve-parser shock: {r.status_code} {r.text[:300]}")
        parsed = self._parse({"date": base.trade_date, **r.json()})
        return ScenarioCurves(base, parsed.shocked)
