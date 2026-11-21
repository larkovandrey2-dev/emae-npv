"""Deal sources: where the orchestrator pulls the portfolio from.

Sources stream: the file (or HTTP body) is read row by row and handed out in batches,
so a portfolio of millions of deals never sits in memory at once.
"""

from __future__ import annotations

import csv
import heapq
import threading
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx

from ..domain.portfolio import DealRecord

TOP_DEALS = 500
TABLE_FIELDS = ("deal_id", "currency_id", "discriminator", "rate_type_cd", "current_principal_amt",
                "interest_rate", "maturity_dttm", "product")


def _rows(lines: Iterable[str]) -> Iterator[DealRecord]:
    """Parses `;` CSV lines; drops empty fields so batches stay small on the wire."""
    reader = csv.DictReader(lines, delimiter=";")
    if not reader.fieldnames or "deal_id" not in [f.lstrip("﻿") for f in reader.fieldnames]:
        raise ValueError("источник сделок вернул CSV без колонки deal_id")
    reader.fieldnames = [f.lstrip("﻿").strip() for f in reader.fieldnames]
    for row in reader:
        yield {k: v.strip() for k, v in row.items() if k and v and v.strip()}


def _batches(rows: Iterator[DealRecord], size: int) -> Iterator[list[DealRecord]]:
    batch: list[DealRecord] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _num(v: str | None) -> float:
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def summarize(rows: Iterable[DealRecord]) -> dict[str, Any]:
    """One pass over the source: what the portfolio page and the launch form need."""
    count = 0
    report_dates: set[str] = set()
    currencies: dict[str, int] = {}
    principal = rate_weighted = floating = 0.0
    rub = 0
    by_year: dict[int, float] = {}
    by_product: dict[str, dict[str, float]] = {}
    tops: dict[str, list[tuple[float, int, dict]]] = {"all": [], "FIXED": [], "FLOAT": []}
    for i, r in enumerate(rows):
        count += 1
        report_dates.add((r.get("report_dttm") or "")[:10])
        ccy = r.get("currency_id", "")
        currencies[ccy] = currencies.get(ccy, 0) + 1
        amount = _num(r.get("current_principal_amt"))
        if ccy == "RUB":
            rub += 1
            principal += amount
            rate_weighted += amount * _num(r.get("interest_rate"))
            if r.get("rate_type_cd") == "FLOAT":
                floating += amount
            year = (r.get("maturity_dttm") or "")[:4]
            if year.isdigit():
                by_year[int(year)] = by_year.get(int(year), 0.0) + amount
            prod = by_product.setdefault(r.get("product") or "", {"count": 0, "principal": 0.0})
            prod["count"] += 1
            prod["principal"] += amount
        item = (amount, -i, {k: r.get(k, "") for k in TABLE_FIELDS})
        for key in ("all", r.get("rate_type_cd", "")):
            heap = tops.get(key)
            if heap is None:
                continue
            if len(heap) < TOP_DEALS:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
    return {
        "count": count,
        "report_dates": sorted(d for d in report_dates if d),
        "currencies": currencies,
        "rub": {
            "count": rub,
            "principal": principal,
            "avg_rate": rate_weighted / principal if principal else 0.0,
            "float_share": floating / principal if principal else 0.0,
            "by_year": sorted(by_year.items()),
            "by_product": [{"product": k, **v} for k, v in sorted(by_product.items(), key=lambda x: -x[1]["principal"])],
        },
        "largest": {k: [x[2] for x in sorted(v, reverse=True)] for k, v in tops.items()},
    }


class _Cached:
    """Summary cache: recomputed when `key()` changes; first call blocks, later ones don't."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key: Any = None
        self._value: dict[str, Any] | None = None

    def get(self, key: Any, compute) -> dict[str, Any]:
        with self._lock:
            if self._value is None or self._key != key:
                self._value, self._key = compute(), key
            return self._value


class CsvFileDealSource:
    def __init__(self, path: str):
        self.path = Path(path)
        self._summary = _Cached()

    def rows(self) -> Iterator[DealRecord]:
        with self.path.open(encoding="utf-8-sig", newline="") as f:
            yield from _rows(f)

    def batches(self, size: int) -> Iterator[list[DealRecord]]:
        return _batches(self.rows(), size)

    def load(self) -> list[DealRecord]:
        return list(self.rows())

    def summary(self) -> dict[str, Any]:
        st = self.path.stat()
        return self._summary.get((st.st_mtime_ns, st.st_size), lambda: summarize(self.rows()))

    def describe(self) -> str:
        return f"file://{self.path}"


class HttpCsvDealSource:
    """A deal store (data mart, S3 presigned URL, …) that serves the deals.csv format."""

    def __init__(self, url: str, timeout_s: float = 300, summary_ttl_s: float = 300):
        self.url = url
        self.timeout_s = timeout_s
        self.summary_ttl_s = summary_ttl_s
        self._summary = _Cached()

    def rows(self) -> Iterator[DealRecord]:
        with httpx.stream("GET", self.url, timeout=self.timeout_s) as r:
            r.raise_for_status()
            yield from _rows(r.iter_lines())

    def batches(self, size: int) -> Iterator[list[DealRecord]]:
        return _batches(self.rows(), size)

    def load(self) -> list[DealRecord]:
        return list(self.rows())

    def summary(self) -> dict[str, Any]:
        return self._summary.get(int(time.time() // self.summary_ttl_s), lambda: summarize(self.rows()))

    def describe(self) -> str:
        return self.url


def deal_source(spec: str):
    if spec.startswith(("http://", "https://")):
        return HttpCsvDealSource(spec)
    return CsvFileDealSource(spec.removeprefix("file://"))
