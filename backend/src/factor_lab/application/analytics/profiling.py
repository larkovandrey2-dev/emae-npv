from __future__ import annotations

from typing import Any

import numpy as np

from .schema import Feature


def profile(X: np.ndarray, y: np.ndarray, features: list[Feature], vocab: dict[str, list[str]],
            groups: np.ndarray, times: np.ndarray) -> dict[str, Any]:
    """Types, missing values, outliers, distributions and redundancy of the inputs."""
    cols = []
    for j, f in enumerate(features):
        col = X[:, j]
        ok = ~np.isnan(col)
        entry: dict[str, Any] = {
            "feature": f.name,
            "source": f.source,
            "kind": f.kind,
            "transform": f.transform,
            "missing_share": float(1 - ok.mean()),
            "unique": int(len(np.unique(col[ok]))),
        }
        if f.kind == "numeric" and ok.any():
            v = col[ok]
            q1, med, q3 = np.percentile(v, [25, 50, 75])
            iqr = q3 - q1
            outliers = ((v < q1 - 3 * iqr) | (v > q3 + 3 * iqr)).mean() if iqr > 0 else 0.0
            entry.update(
                min=float(v.min()), p25=float(q1), median=float(med), p75=float(q3), max=float(v.max()),
                mean=float(v.mean()), std=float(v.std()), outlier_share=float(outliers),
                varies_in_time=bool(_varies_within_groups(col, groups)),
            )
        elif f.kind == "categorical":
            levels, counts = np.unique(col[ok].astype(int), return_counts=True)
            entry["levels"] = {vocab[f.name][int(l)]: int(c) for l, c in zip(levels, counts)}
        cols.append(entry)

    numeric = [j for j, f in enumerate(features) if f.kind == "numeric"]
    redundant = []
    for a_i, a in enumerate(numeric):
        for b in numeric[a_i + 1:]:
            r = _spearman(X[:, a], X[:, b])
            if r is not None and abs(r) >= 0.95:
                redundant.append({"features": [features[a].name, features[b].name], "spearman": r})
    return {
        "rows": int(len(y)),
        "entities": int(len(np.unique(groups))),
        "periods": int(len(np.unique(times))),
        "target": {
            "mean": float(np.mean(y)), "std": float(np.std(y)),
            "min": float(np.min(y)), "max": float(np.max(y)),
        },
        "columns": cols,
        "redundant_pairs": redundant,
    }


def _varies_within_groups(col: np.ndarray, groups: np.ndarray) -> bool:
    for g in np.unique(groups):
        v = col[(groups == g) & ~np.isnan(col)]
        if len(v) > 1 and np.ptp(v) > 1e-12:
            return True
    return False


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    ok = ~np.isnan(a) & ~np.isnan(b)
    if ok.sum() < 5 or np.ptp(a[ok]) == 0 or np.ptp(b[ok]) == 0:
        return None
    ra = np.argsort(np.argsort(a[ok]))
    rb = np.argsort(np.argsort(b[ok]))
    return float(np.corrcoef(ra, rb)[0, 1])
