"""Ports: the capabilities the use cases need. Adapters implement them structurally."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Iterator
from typing import Any, Protocol

from ..domain.curves import Curve, ScenarioCurves
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord, ScheduledFlow


class DealSource(Protocol):
    def batches(self, size: int) -> Iterator[list[DealRecord]]:
        """Deals in source order, `size` at a time; empty fields are left out."""

    def summary(self) -> dict[str, Any]:
        """Portfolio profile for the UI: counts, report dates, RUB aggregates, largest deals."""

    def describe(self) -> str: ...


class CurveService(Protocol):
    def snapshot(self, on_or_before: str) -> ScenarioCurves:
        """Stored base and shocked curves of the latest trade date not after the given date."""

    def shock(self, base: Curve) -> ScenarioCurves:
        """IRRBB shocks applied to an arbitrary base curve."""

    def health(self) -> bool: ...


@dataclass
class CalculatorResult:
    valuation: Valuation
    schedules: dict[str, list[ScheduledFlow]] = field(default_factory=dict)


@dataclass
class DealValues:
    outstanding: float
    npv: list[dict[str, float]]  # one {scenario: npv} per requested curve set


@dataclass
class PortfolioValuation:
    """Totals of one valuation request; per-deal values only for the deals asked for."""

    as_of: str
    valued: int
    matured: int
    failed_total: int
    failures: dict[str, str]
    totals: dict[str, dict[str, float]]  # curve set name -> {scenario: npv}
    deals: dict[str, DealValues] = field(default_factory=dict)


CurveSets = list[tuple[str, dict[str, Curve]]]


class PortfolioSession(Protocol):
    """A portfolio held by the calculator: uploaded once, valued many times."""

    def upload(self, deals: list[DealRecord], currency: str) -> dict[str, Any]: ...

    def seal(self, dates: list[str]) -> dict[str, Any]:
        """Builds base schedules and balances on `dates`; returns valid/failed counts."""

    def value(self, as_of: str, sets: CurveSets, sample_ids: list[str] | None = None,
              only_ids: list[str] | None = None, keep: str | None = None) -> PortfolioValuation:
        """Rolls deals to `as_of` and values them under every curve set."""

    def movers(self, from_key: str, to_key: str, n: int) -> list[dict[str, Any]]:
        """Deals with the largest base NPV change between two kept valuations."""

    def close(self) -> None: ...


class Calculator(Protocol):
    def open_portfolio(self, prepayment: dict[str, Any] | None = None) -> PortfolioSession:
        """`prepayment`: {"enabled": bool, "rates": {model code: annual CPR}}; None — calculator default."""

    def health(self) -> bool: ...


class RunRepository(Protocol):
    def create(self, request: dict[str, Any]) -> str: ...

    def progress(self, run_id: str, stage: str, progress: float) -> None: ...

    def complete(self, run_id: str, result: dict[str, Any]) -> None: ...

    def fail(self, run_id: str, error: str) -> None: ...

    def get(self, run_id: str) -> dict[str, Any] | None: ...

    def list(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def delete(self, run_id: str) -> bool: ...

    def save_explanation(self, run_id: str, record: dict[str, Any]) -> dict[str, Any]: ...

    def explanations(self, run_id: str) -> list[dict[str, Any]]: ...


class Explainer(Protocol):
    """LLM layer: turns verified facts into text. It never computes numbers."""

    name: str
    model: str | None

    def explain(self, facts: dict[str, Any], question: str | None) -> dict[str, Any]:
        """Returns {"headline": str, "blocks": [{"type", "text", "fact_ids", "chart", "metric", "tone"}],
        "follow_ups": [str], "confidence": "high|medium|low"}; numbers appear only as {{F#}} placeholders."""

    def available(self) -> bool: ...
