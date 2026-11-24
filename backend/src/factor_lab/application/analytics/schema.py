"""Generic feature discovery: no fixed list of factors.

Every column of an arbitrary table is classified as numeric, date (turned into years
relative to the observation date), categorical or dropped with a stated reason. The
same code works for any target metric and any set of attributes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
MAX_MISSING = 0.6
MAX_LEVELS = 30


@dataclass(frozen=True)
class Feature:
    name: str
    kind: str  # numeric | categorical
    source: str  # original column
    transform: str  # identity | years_from_observation | category


def _missing(v: object) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, float) and math.isnan(v))


def _num(v: object) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return None


def infer_schema(
    rows: list[dict],
    *,
    exclude: set[str],
    group_col: str,
    time_col: str,
) -> tuple[list[Feature], list[dict]]:
    """Returns usable features and a log of dropped columns with reasons."""
    if not rows:
        return [], []
    columns: list[str] = []
    for r in rows:
        for c in r:
            if c not in columns:
                columns.append(c)
    features: list[Feature] = []
    dropped: list[dict] = []
    for col in columns:
        if col in exclude or col in (group_col, time_col):
            continue
        values = [r.get(col) for r in rows]
        present = [v for v in values if not _missing(v)]
        missing_share = 1 - len(present) / len(values)
        if missing_share > MAX_MISSING:
            dropped.append({"column": col, "reason": f"{missing_share:.0%} missing"})
            continue
        distinct = {str(v) for v in present}
        if len(distinct) <= 1:
            dropped.append({"column": col, "reason": "constant"})
            continue
        if col.endswith("_id") or col == "id":
            dropped.append({"column": col, "reason": "identifier"})
            continue
        if all(isinstance(v, str) and DATE_RE.match(v) for v in present):
            features.append(Feature(f"{col}__years", "numeric", col, "years_from_observation"))
            continue
        nums = [_num(v) for v in present]
        if sum(x is not None for x in nums) >= 0.95 * len(present):
            features.append(Feature(col, "numeric", col, "identity"))
            continue
        if len(distinct) > MAX_LEVELS:
            dropped.append({"column": col, "reason": f"too many categories ({len(distinct)})"})
            continue
        features.append(Feature(col, "categorical", col, "category"))
    return features, dropped


def encode(rows: list[dict], features: list[Feature], time_col: str):
    """Numeric matrix (NaN for missing) and the category vocabularies."""
    import numpy as np

    vocab: dict[str, list[str]] = {}
    for f in features:
        if f.kind == "categorical":
            levels = sorted({str(r.get(f.source)) for r in rows if not _missing(r.get(f.source))})
            vocab[f.name] = levels
    X = np.full((len(rows), len(features)), np.nan)
    for i, r in enumerate(rows):
        obs = r.get(time_col)
        for j, f in enumerate(features):
            v = r.get(f.source)
            if _missing(v):
                continue
            if f.transform == "years_from_observation":
                try:
                    X[i, j] = (date.fromisoformat(str(v)[:10]) - date.fromisoformat(str(obs)[:10])).days / 365.0
                except ValueError:
                    pass
            elif f.kind == "numeric":
                x = _num(v)
                if x is not None and math.isfinite(x):
                    X[i, j] = x
            else:
                X[i, j] = vocab[f.name].index(str(v))
    return X, vocab
