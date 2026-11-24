"""Builds the entity × period table the factor model learns from."""

from __future__ import annotations

from typing import Any

from ...domain.curves import BASE
from ...domain.metrics import Valuation
from ...domain.portfolio import DealRecord

# Business metadata for the LLM and the UI; unknown columns keep their raw name.
FIELD_LABELS = {
    "current_principal_amt": "Balance",
    "start_principal_amt": "Original amount",
    "interest_rate": "Deal rate",
    "base_indicator_rate": "Index base value",
    "discriminator": "Schedule",
    "rate_type_cd": "Rate type",
    "product": "Product",
    "behavior_model_cd": "Prepayment model",
    "day_convention_cd": "Day count",
    "maturity_dttm": "Years to maturity",
    "deal_dttm": "Age, years",
    "interest_payment_rule_start_dttm": "Interest start, years",
    "regular_payment_rule_start_dttm": "Repayment start, years",
    "repricing_rule_start_dttm": "Repricing start, years",
    "interest_payment_rule_day_num": "Interest day",
    "regular_payment_rule_day_num": "Repayment day",
    "interest_payment_rule_period_cd": "Interest frequency",
    "regular_payment_rule_period_cd": "Repayment frequency",
    "repricing_rule_period_cd": "Repricing frequency",
    "sub_rate_type_cd": "Subsidy rate type",
    "sub_interest_rate": "Subsidy rate",
    "cap_limit_interest_rate": "Cap",
    "floor_limit_interest_rate": "Floor",
    "accrued_interest": "Accrued interest",
    "curve_level": "Curve level",
    "curve_slope": "Curve slope (10Y − 3M)",
    "curve_curvature": "Curve curvature",
    "currency_id": "Currency",
}

TARGETS = {
    "dnpv_worst_per_rub": "ΔNPV per ₽",
    "dnpv_worst": "Worst ΔNPV",
    "npv_base": "Base NPV",
}

SKIP_COLUMNS = {"report_dttm", "deal_report_dttm", "interest_payment", "principal_payment",
                "repricing_dttm_schedule", "repricing_dttm_rate_schedule",
                "repricing_dttm_rate_schedule_fixed"}


def build_panel(periods: list[tuple[str, list[DealRecord], Valuation, dict[str, float]]]) -> list[dict[str, Any]]:
    """One row per deal and report date: every deal attribute, curve shape and target values."""
    rows = []
    for period, deals, valuation, curve_features in periods:
        worst = valuation.worst_scenario
        for deal in deals:
            ident = deal["deal_id"]
            npv = valuation.deal_npv.get(ident)
            if npv is None:
                continue
            row: dict[str, Any] = {k: v for k, v in deal.items() if k not in SKIP_COLUMNS}
            row["period"] = period
            row.update(curve_features)
            row["npv_base"] = npv[BASE]
            row["dnpv_worst"] = npv[worst] - npv[BASE]
            principal = float(deal.get("current_principal_amt") or 0)
            if principal > 0:
                row["dnpv_worst_per_rub"] = row["dnpv_worst"] / principal
            for scenario, value in npv.items():
                if scenario != BASE:
                    row[f"dnpv_{scenario.lower()}"] = value - npv[BASE]
            rows.append(row)
    return rows
