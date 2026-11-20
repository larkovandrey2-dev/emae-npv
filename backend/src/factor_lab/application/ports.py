"""Ports: the capabilities the use cases need. Adapters implement them structurally."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..domain.curves import Curve, ScenarioCurves
from ..domain.metrics import Valuation
from ..domain.portfolio import DealRecord, ScheduledFlow


class DealSource(Protocol):
    def load(self) -> list[DealRecord]: ...

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


class Calculator(Protocol):
    def value(
        self,
        deals: list[DealRecord],
        curves: dict[str, Curve],
        report_date: str,
        include_cashflows: bool = False,
    ) -> CalculatorResult: ...

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
