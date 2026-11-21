from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class RunCreate(BaseModel):
    start: date | None = None
    end: date | None = None
    frequency: Literal["month", "quarter"] = "month"
    dates: list[date] = Field(default_factory=list, max_length=40)
    target: str = Field("dnpv_worst_per_rub", max_length=40, pattern=r"^[a-z_]+$")
    explain: bool = True
    # generated portfolio id; absent or "default" — the configured deal source
    dataset: str | None = Field(None, max_length=40)
    prepayment: bool = True
    prepayment_rates: dict[str, float] = Field(default_factory=dict)

    @field_validator("prepayment_rates")
    @classmethod
    def cpr_range(cls, v):
        for code, cpr in v.items():
            if not 0 <= cpr < 1:
                raise ValueError(f"CPR для {code} должен быть в [0; 1)")
        return v

    @field_validator("end")
    @classmethod
    def after_start(cls, v, info):
        start = info.data.get("start")
        if v and start and v <= start:
            raise ValueError("end must be after start")
        return v


class DatasetCreate(BaseModel):
    name: str = Field("", max_length=120)
    params: dict[str, Any] = Field(default_factory=dict)


class QuestionCreate(BaseModel):
    question: str | None = Field(None, max_length=2000)
