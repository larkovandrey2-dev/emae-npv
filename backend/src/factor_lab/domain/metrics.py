from __future__ import annotations

from dataclasses import dataclass, field

from .curves import BASE


@dataclass
class Valuation:
    """NPV of a portfolio on one report date under the base and every stress scenario."""

    report_date: str
    curve_date: str
    npv: dict[str, float]
    deal_npv: dict[str, dict[str, float]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def npv_base(self) -> float:
        return self.npv[BASE]

    def dnpv(self) -> dict[str, float]:
        base = self.npv[BASE]
        return {s: v - base for s, v in self.npv.items() if s != BASE}

    @property
    def ec(self) -> float:
        """EC proxy: the minimum ΔNPV over all stress scenarios (negative is a loss)."""
        return min(self.dnpv().values())

    @property
    def worst_scenario(self) -> str:
        d = self.dnpv()
        return min(d, key=d.get)

    def metric(self, name: str) -> float:
        if name == "npv_base":
            return self.npv_base
        if name == "ec":
            return self.ec
        if name.startswith("dnpv_"):
            return self.dnpv()[name.removeprefix("dnpv_").upper()]
        raise KeyError(name)

    def deal_dnpv(self, deal_id: str, scenario: str) -> float:
        row = self.deal_npv[deal_id]
        return row[scenario] - row[BASE]


METRICS = {
    "npv_base": "Base NPV",
    "ec": "EC: minimum ΔNPV over stress scenarios",
}
