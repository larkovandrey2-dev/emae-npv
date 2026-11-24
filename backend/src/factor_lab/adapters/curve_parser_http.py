"""Curve service adapter: curve-parser HTTP API (MOEX G-curve + IRRBB shocks)."""

from __future__ import annotations

import threading
from datetime import date, timedelta

import httpx

from ..domain.curves import Curve, CurvePoint, ScenarioCurves

LOOKBACK_DAYS = 10


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
            raise CurveServiceError(f"curve service unavailable: {exc}") from exc
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
        tenors = sorted(payload["tenors"], key=lambda t: t["tenor_years"])
        base = Curve(payload.get("date", ""), tuple(CurvePoint(t["tenor_years"], t["base_rate"]) for t in tenors))
        shocked: dict[str, list[CurvePoint]] = {}
        for t in tenors:
            for s in t["scenarios"]:
                shocked.setdefault(_scenario_name(s["scenario"]), []).append(CurvePoint(t["tenor_years"], s["rate"]))
        return ScenarioCurves(base, {k: Curve(base.trade_date, tuple(v)) for k, v in shocked.items()})

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
            raise CurveServiceError(f"no MOEX curve for {start}…{on_or_before}")
        trade = max(found)
        r = self._get(f"/api/v1/curves/{trade}")
        if r.status_code != 200:
            raise CurveServiceError(f"curve-parser /curves/{trade}: {r.status_code} {r.text[:300]}")
        curves = self._parse(r.json())
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
