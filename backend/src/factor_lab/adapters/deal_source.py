"""Deal sources: where the orchestrator pulls the portfolio from."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import httpx

from ..domain.portfolio import DealRecord


def _parse(text: str) -> list[DealRecord]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    if not rows or "deal_id" not in rows[0]:
        raise ValueError("источник сделок вернул CSV без колонки deal_id")
    return [{k: (v or "").strip() for k, v in r.items()} for r in rows]


class CsvFileDealSource:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> list[DealRecord]:
        return _parse(self.path.read_text(encoding="utf-8"))

    def describe(self) -> str:
        return f"file://{self.path}"


class HttpCsvDealSource:
    """A deal store (data mart, S3 presigned URL, …) that serves the deals.csv format."""

    def __init__(self, url: str, timeout_s: float = 30):
        self.url = url
        self.timeout_s = timeout_s

    def load(self) -> list[DealRecord]:
        r = httpx.get(self.url, timeout=self.timeout_s)
        r.raise_for_status()
        return _parse(r.text)

    def describe(self) -> str:
        return self.url


def deal_source(spec: str):
    if spec.startswith(("http://", "https://")):
        return HttpCsvDealSource(spec)
    return CsvFileDealSource(spec.removeprefix("file://"))
