from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RunCreate(BaseModel):
    start: date | None = None
    end: date | None = None
    frequency: Literal["month", "quarter"] = "month"
    dates: list[date] = Field(default_factory=list, max_length=40)
    target: str = Field("dnpv_worst_per_rub", max_length=40, pattern=r"^[a-z_]+$")
    explain: bool = True

    @field_validator("end")
    @classmethod
    def after_start(cls, v, info):
        start = info.data.get("start")
        if v and start and v <= start:
            raise ValueError("end must be after start")
        return v


class QuestionCreate(BaseModel):
    question: str | None = Field(None, max_length=2000)
