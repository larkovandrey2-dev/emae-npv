"""Builds the entity × period table the factor model learns from."""

from __future__ import annotations

from typing import Any

from ...domain.curves import BASE
from ...domain.metrics import Valuation
from ...domain.portfolio import DealRecord

# Business metadata for the LLM and the UI; unknown columns keep their raw name.
FIELD_LABELS = {
    "current_principal_amt": "Остаток основного долга",
    "start_principal_amt": "Начальная сумма",
    "interest_rate": "Ставка по сделке",
    "base_indicator_rate": "Базовое значение индикатора",
    "discriminator": "Схема погашения",
    "rate_type_cd": "Тип ставки",
    "product": "Продукт",
    "behavior_model_cd": "Поведенческая модель",
    "day_convention_cd": "Базис начисления",
    "maturity_dttm": "Срок до погашения, лет",
    "deal_dttm": "Дата выдачи, лет до наблюдения (со знаком минус)",
    "interest_payment_rule_start_dttm": "Начало графика процентов, лет до наблюдения",
    "regular_payment_rule_start_dttm": "Начало графика погашений, лет до наблюдения",
    "repricing_rule_start_dttm": "Начало графика переоценки, лет до наблюдения",
    "interest_payment_rule_day_num": "День уплаты процентов",
    "regular_payment_rule_day_num": "День погашения долга",
    "interest_payment_rule_period_cd": "Периодичность процентов",
    "regular_payment_rule_period_cd": "Периодичность погашения",
    "repricing_rule_period_cd": "Периодичность переоценки",
    "sub_rate_type_cd": "Тип дополнительной ставки",
    "sub_interest_rate": "Дополнительная ставка",
    "cap_limit_interest_rate": "Cap",
    "floor_limit_interest_rate": "Floor",
    "accrued_interest": "Начисленные проценты",
    "curve_level": "Уровень кривой",
    "curve_slope": "Наклон кривой (10 лет − 3 мес.)",
    "curve_curvature": "Кривизна кривой",
    "currency_id": "Валюта",
}

TARGETS = {
    "dnpv_worst_per_rub": "ΔNPV в худшем сценарии на рубль остатка (чувствительность сделки)",
    "dnpv_worst": "ΔNPV сделки в худшем для портфеля сценарии периода",
    "npv_base": "NPV сделки в базовом сценарии",
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
