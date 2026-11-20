from __future__ import annotations
from decimal import Decimal, InvalidOperation
from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator

Scheme = Literal["ANN", "RGM", "SIMPLE"]
Factor = Literal["amount", "rate", "term", "scheme", "day_count", "payment_day", "payment_frequency", "accrued_interest"]


def dec(v: object) -> Decimal:
    try:
        x = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Введите корректное десятичное число")
    if not x.is_finite():
        raise ValueError("Число должно быть конечным")
    return x


def money(v: Decimal) -> str:
    return format(v.quantize(Decimal("0.01")), "f")


class DealCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    current_principal_amt: str
    interest_rate: str
    maturity_dttm: date
    discriminator: Scheme

    @field_validator("current_principal_amt")
    @classmethod
    def positive_amt(cls, v):
        x = dec(v)
        if x <= 0:
            raise ValueError("Остаток должен быть положительным")
        if x > Decimal("1000000000000000") or max(0, -x.as_tuple().exponent) > 2:
            raise ValueError(
                "Остаток должен быть не больше 1 квадриллиона рублей и с точностью до копеек"
            )
        return format(x, "f")

    @field_validator("interest_rate")
    @classmethod
    def valid_rate(cls, v):
        x = dec(v)
        if x < 0 or x > 1 or max(0, -x.as_tuple().exponent) > 8:
            raise ValueError(
                "Ставка должна быть долей от 0 до 1 с точностью до 8 знаков"
            )
        return format(x, "f")

    @field_validator("name")
    @classmethod
    def trim_name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v

    @field_validator("maturity_dttm")
    @classmethod
    def valid_date(cls, v):
        if v <= date(2022, 8, 31) or v > date(2052, 8, 31):
            raise ValueError(
                "Срок должен быть после даты оценки и не позднее 31.08.2052"
            )
        return v


class ValuationRequest(BaseModel):
    deal_ids: list[str] = Field(min_length=1, max_length=10)
    profile_id: str
    act_act_as_365: bool = False


class ExperimentRequest(ValuationRequest):
    target_deal_id: str
    factor: Factor
    values: list[str] = Field(min_length=1, max_length=21)

    @field_validator("values")
    @classmethod
    def unique_values(cls, v):
        if len(set(v)) != len(v):
            raise ValueError("Значения серии должны быть уникальными")
        if any(len(value) > 64 for value in v):
            raise ValueError("Значение фактора не должно превышать 64 символа")
        return v


class InteractionRequest(ValuationRequest):
    target_deal_id: str
    factor_a: Factor
    value_a: str
    factor_b: Factor
    value_b: str
    base_value_a: str | None = None
    base_value_b: str | None = None


class DiscountExperimentRequest(ValuationRequest):
    factor: Literal["parallel_shift", "stress_shift", "long_end_shift"]
    values: list[str] = Field(min_length=1, max_length=21)

    @field_validator("values")
    @classmethod
    def valid_shifts(cls, values):
        normalized = []
        for value in values:
            if len(value) > 64:
                raise ValueError("Сдвиг не должен превышать 64 символа")
            amount = dec(value)
            if max(0, -amount.as_tuple().exponent) > 6 or abs(amount) > 500:
                raise ValueError("Сдвиг кривой должен быть от −500 до +500 б.п.")
            normalized.append(amount)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Значения серии должны быть уникальными численно")
        return [format(x, "f") for x in normalized]
