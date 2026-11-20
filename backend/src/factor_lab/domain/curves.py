from __future__ import annotations

from dataclasses import dataclass, field
from bisect import bisect_left

BASE = "BASE"

# Tenor buckets used as curve factors: (exclusive lower, inclusive upper] in years.
CURVE_BUCKETS: dict[str, tuple[float, float]] = {
    "curve_short": (0.0, 1.0),
    "curve_medium": (1.0, 5.0),
    "curve_long": (5.0, float("inf")),
}


@dataclass(frozen=True)
class CurvePoint:
    tenor_years: float
    rate: float  # zero rate as a fraction, annual compounding (MOEX G-curve yield)


@dataclass(frozen=True)
class Curve:
    trade_date: str
    points: tuple[CurvePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("curve has no points")
        tenors = [p.tenor_years for p in self.points]
        if tenors != sorted(tenors) or len(set(tenors)) != len(tenors):
            raise ValueError("curve tenors must be strictly increasing")

    def rate_at(self, years: float) -> float:
        pts = self.points
        tenors = [p.tenor_years for p in pts]
        if years <= tenors[0]:
            return pts[0].rate
        if years >= tenors[-1]:
            return pts[-1].rate
        i = bisect_left(tenors, years)
        left, right = pts[i - 1], pts[i]
        w = (years - left.tenor_years) / (right.tenor_years - left.tenor_years)
        return left.rate + w * (right.rate - left.rate)

    def features(self) -> dict[str, float]:
        """Shape descriptors used as explanatory factors."""
        r1, r5, r10 = self.rate_at(1), self.rate_at(5), self.rate_at(10)
        return {
            "curve_level": sum(p.rate for p in self.points) / len(self.points),
            "curve_slope": r10 - self.rate_at(0.25),
            "curve_curvature": 2 * r5 - r1 - r10,
        }

    def key(self) -> tuple[tuple[float, float], ...]:
        return tuple((p.tenor_years, round(p.rate, 12)) for p in self.points)


@dataclass(frozen=True)
class ScenarioCurves:
    """Base curve plus the IRRBB-shocked curves built from it by the curve service."""

    base: Curve
    shocked: dict[str, Curve] = field(default_factory=dict)

    def all(self) -> dict[str, Curve]:
        return {BASE: self.base, **self.shocked}


def bucket_of(tenor_years: float) -> str:
    for name, (lo, hi) in CURVE_BUCKETS.items():
        if lo < tenor_years <= hi:
            return name
    raise ValueError(f"tenor {tenor_years} has no bucket")


def blend(start: Curve, end: Curve, moved: set[str]) -> Curve:
    """Start curve with the nodes of the `moved` tenor buckets taken from `end`.

    Both curves share the tenor grid (the curve service publishes a fixed grid).
    """
    if [p.tenor_years for p in start.points] != [p.tenor_years for p in end.points]:
        raise ValueError("curves have different tenor grids")
    points = tuple(
        e if bucket_of(s.tenor_years) in moved else s
        for s, e in zip(start.points, end.points)
    )
    trade_date = end.trade_date if moved else start.trade_date
    return Curve(trade_date=trade_date, points=points)
