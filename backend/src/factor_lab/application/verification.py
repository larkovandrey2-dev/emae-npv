"""Fact check of LLM text: every number must trace back to a fact."""

from __future__ import annotations

import re
from typing import Any

NUMBER = re.compile(
    r"(?<![\w\[])([-−–]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|[-−–]?\d+(?:[.,]\d+)?)"
    r"\s*(млрд|млн|тыс\.?|%|п\.\s?п\.|б\.\s?п\.|bp)?",
    re.IGNORECASE,
)
DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}")
FACT_REF = re.compile(r"\[?F\d+\]?")
MULT = {"млрд": 1e9, "млн": 1e6, "тыс": 1e3, "тыс.": 1e3}


def _candidates(fact: dict[str, Any]) -> list[float]:
    v = fact.get("value")
    if not isinstance(v, (int, float)):
        return []
    v = float(v)
    if fact["unit"] in ("share", "ratio"):
        return [v, v * 100]
    if fact["unit"] == "value":
        return [v, v * 100, v * 10000]
    return [v]


def _parse(raw: str, suffix: str | None) -> tuple[float, float]:
    text = raw.replace("−", "-").replace("–", "-")
    for sp in (" ", " ", " "):
        text = text.replace(sp, "")
    text = text.replace(",", ".")
    value = float(text)
    decimals = len(text.split(".")[1]) if "." in text else 0
    mult = MULT.get((suffix or "").lower().rstrip(), 1.0)
    if (suffix or "").lower().startswith("б"):
        mult = 1.0
    precision = 0.5 * 10 ** (-decimals) * mult
    return value * mult, precision


def verify_text(texts: list[str], facts: list[dict[str, Any]]) -> dict[str, Any]:
    allowed = [c for f in facts for c in _candidates(f)]
    known_ids = {f["id"] for f in facts}
    unverified, checked = [], 0
    cited = set()
    for text in texts:
        cited |= set(re.findall(r"F\d+", text))
        clean = FACT_REF.sub(" ", DATE.sub(" ", text))
        for m in NUMBER.finditer(clean):
            raw, suffix = m.group(1), m.group(2)
            try:
                value, precision = _parse(raw, suffix)
            except ValueError:
                continue
            if suffix is None and float(value).is_integer() and (abs(value) <= 30 or 1900 <= value <= 2100):
                continue  # counts, tenors, years
            checked += 1
            tol = lambda c: max(precision * 1.01, abs(c) * 0.006)  # noqa: E731
            if not any(abs(abs(value) - abs(c)) <= tol(c) for c in allowed):
                unverified.append(m.group(0).strip())
    unknown = sorted(cited - known_ids)
    status = "ok" if not unverified and not unknown else "warnings"
    return {"status": status, "checked_numbers": checked, "unverified_numbers": unverified,
            "unknown_fact_ids": unknown, "cited_fact_ids": sorted(cited & known_ids)}
