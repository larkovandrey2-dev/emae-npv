"""Model-agnostic factor analysis on a panel (entity × period) table.

Pipeline: schema discovery → profiling → linear baseline vs gradient boosting (grouped CV)
→ global permutation importance → partial-dependence shapes (threshold, saturation,
U-shape) → Friedman H interactions → local Shapley contributions → stability checks →
reliability verdict. Works for any numeric target and any attribute set.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.linear_model import RidgeCV
from sklearn.metrics import r2_score
from sklearn.model_selection import GroupKFold

from .profiling import profile
from .schema import encode, infer_schema

MIN_ROWS = 60
MIN_ENTITIES = 8
MIN_PERIODS = 3
GOOD_R2 = 0.6
STABLE_RANK = 0.7
TOP_SHAPES = 6
TOP_INTERACT = 5


@dataclass(frozen=True)
class PanelSpec:
    target: str
    group_col: str = "deal_id"
    time_col: str = "period"
    exclude_prefixes: tuple[str, ...] = ("npv_", "dnpv_", "target")


def _model(seed: int, **overrides) -> HistGradientBoostingRegressor:
    params = dict(max_iter=300, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=8,
                  l2_regularization=1e-3, random_state=seed)
    params.update(overrides)
    return HistGradientBoostingRegressor(**params)


class _Pipe:
    """Gradient boosting with categorical columns flagged natively."""

    def __init__(self, cat_mask: np.ndarray, seed: int, **overrides):
        self.cat_mask = cat_mask
        self.model = _model(seed, categorical_features=cat_mask if cat_mask.any() else None, **overrides)

    def fit(self, X, y):
        self.model.fit(X, y)
        return self

    def predict(self, X):
        return self.model.predict(X)



class _Linear:
    """Ridge on standardized numeric (median-imputed) and one-hot categorical columns."""

    def __init__(self, cat_mask: np.ndarray, vocab_sizes: list[int]):
        self.cat_mask = cat_mask
        self.sizes = vocab_sizes

    def _design(self, X):
        parts = []
        for j in range(X.shape[1]):
            col = X[:, j]
            if self.cat_mask[j]:
                onehot = np.zeros((len(col), max(self.sizes[j], 1)))
                ok = ~np.isnan(col)
                onehot[np.where(ok)[0], col[ok].astype(int)] = 1
                parts.append(onehot)
            else:
                filled = np.where(np.isnan(col), self.med[j], col)
                parts.append(((filled - self.mu[j]) / self.sd[j])[:, None])
        return np.hstack(parts)

    def fit(self, X, y):
        self.med = np.nanmedian(np.where(self.cat_mask, 0, X), axis=0)
        self.med = np.where(np.isnan(self.med), 0, self.med)
        filled = np.where(np.isnan(X), self.med, X)
        self.mu = filled.mean(axis=0)
        self.sd = np.where(filled.std(axis=0) > 0, filled.std(axis=0), 1.0)
        self.model = RidgeCV(alphas=np.logspace(-3, 3, 13)).fit(self._design(X), y)
        return self

    def predict(self, X):
        return self.model.predict(self._design(X))


def _rank_corr(a: np.ndarray, b: np.ndarray) -> float:
    if np.ptp(a) == 0 or np.ptp(b) == 0:
        return 1.0 if np.allclose(a, b) else 0.0
    ra, rb = np.argsort(np.argsort(-a)), np.argsort(np.argsort(-b))
    return float(np.corrcoef(ra, rb)[0, 1])


def _pd_1d(predict, X, j, grid):
    return _pd_at(predict, X, [j], np.asarray(grid, dtype=float)[:, None])


def _grid(col: np.ndarray, categorical: bool, size: int = 20) -> np.ndarray:
    v = col[~np.isnan(col)]
    if categorical:
        return np.unique(v)
    g = np.unique(np.percentile(v, np.linspace(2, 98, size)))
    return g


def classify_shape(x: np.ndarray, y: np.ndarray, target_std: float) -> dict[str, Any]:
    """Names the dependence form of a partial-dependence curve."""
    span = float(y.max() - y.min()) if len(y) else 0.0
    if len(x) < 4 or span <= 0.02 * max(target_std, 1e-12):
        return {"shape": "flat", "effect_range": span, "nonlinearity": 0.0}
    coef = np.polyfit(x, y, 1)
    fit = np.polyval(coef, x)
    ss_res, ss_tot = float(((y - fit) ** 2).sum()), float(((y - y.mean()) ** 2).sum())
    nonlin = ss_res / ss_tot if ss_tot > 0 else 0.0
    steps = np.diff(y)
    n = len(y)
    edge = max(1, int(round(0.15 * n)))
    i_min, i_max = int(np.argmin(y)), int(np.argmax(y))
    result: dict[str, Any] = {"effect_range": span, "nonlinearity": nonlin, "slope_sign": int(np.sign(coef[0]))}
    for idx, kind in ((i_min, "u_shape"), (i_max, "inverted_u")):
        if edge <= idx < n - edge:
            left = abs(y[0] - y[idx]) / span
            right = abs(y[-1] - y[idx]) / span
            if left > 0.2 and right > 0.2:
                result.update(shape=kind, turning_point=float(x[idx]))
                return result
    big = int(np.argmax(np.abs(steps)))
    # A jump at the edge of the range is saturation, not a threshold: require flat ground on both sides.
    if edge <= big < n - 1 - edge and abs(steps[big]) >= 0.4 * span and nonlin > 0.05:
        result.update(shape="threshold", threshold=float((x[big] + x[big + 1]) / 2))
        return result
    third = max(1, (n - 1) // 3)
    first = abs(y[third] - y[0]) / max(x[third] - x[0], 1e-12)
    last = abs(y[-1] - y[-1 - third]) / max(x[-1] - x[-1 - third], 1e-12)
    if nonlin > 0.05 and max(first, last) > 0 and min(first, last) < 0.25 * max(first, last):
        result.update(shape="saturation" if last < first else "acceleration")
        return result
    result["shape"] = "linear" if nonlin <= 0.05 else "nonlinear_monotonic"
    return result


def _pd_at(predict, X, cols, values) -> np.ndarray:
    """Partial dependence at each row of `values` (k × len(cols)), one batched prediction."""
    n = len(X)
    big = np.repeat(X[None, :, :], len(values), axis=0)
    for c_i, c in enumerate(cols):
        big[:, :, c] = values[:, c_i][:, None]
    return predict(big.reshape(-1, X.shape[1])).reshape(len(values), n).mean(axis=1)


def _h_statistic(predict, X, a, b):
    """Friedman H² for a pair, on a subsample, with centered partial dependences."""
    pd_ab = _pd_at(predict, X, [a, b], X[:, [a, b]])
    pd_a = _pd_at(predict, X, [a], X[:, [a]])
    pd_b = _pd_at(predict, X, [b], X[:, [b]])
    pd_ab, pd_a, pd_b = pd_ab - pd_ab.mean(), pd_a - pd_a.mean(), pd_b - pd_b.mean()
    denom = float((pd_ab ** 2).sum())
    if denom <= 0:
        return 0.0
    return float(((pd_ab - pd_a - pd_b) ** 2).sum() / denom)


def _conditional_effect(predict, X, a, b, cat_b: bool):
    """Effect range of feature a when b is low vs high (or at two frequent levels)."""
    col_a, col_b = X[:, a], X[:, b]
    grid = _grid(col_a, False, 10)
    if cat_b:
        vals, counts = np.unique(col_b[~np.isnan(col_b)], return_counts=True)
        order = np.argsort(-counts)
        levels = [vals[order[0]], vals[order[min(1, len(order) - 1)]]]
    else:
        levels = list(np.nanpercentile(col_b, [25, 75]))
    effects = []
    for lv in levels:
        Xc = X.copy()
        Xc[:, b] = lv
        pd = _pd_1d(predict, Xc, a, grid)
        effects.append(float(pd[-1] - pd[0]))
    return {"b_low": float(levels[0]), "b_high": float(levels[1]),
            "effect_of_a_at_b_low": effects[0], "effect_of_a_at_b_high": effects[1]}


def _local_shapley(predict, X, row: np.ndarray, feats: list[int], rng, perms=64, bg=48):
    """Interventional Shapley by permutation sampling for one observation (batched per permutation)."""
    background = X[rng.choice(len(X), size=min(bg, len(X)), replace=False)]
    k = len(feats)
    phi = np.zeros(k)
    for _ in range(perms):
        order = rng.permutation(k)
        stack = np.repeat(background[None, :, :], k + 1, axis=0)
        for step, f_i in enumerate(order, start=1):
            stack[step:, :, feats[f_i]] = row[feats[f_i]]
        means = predict(stack.reshape(-1, X.shape[1])).reshape(k + 1, len(background)).mean(axis=1)
        phi[order] += np.diff(means)
    phi /= perms
    base = float(predict(background).mean())
    return base, phi


def analyze_panel(rows: list[dict], spec: PanelSpec, labels: dict[str, str] | None = None,
                  seed: int = 7) -> dict[str, Any]:
    labels = labels or {}
    exclude = {c for r in rows[:1] for c in r if c.startswith(spec.exclude_prefixes)} | {spec.target}
    rows = [r for r in rows if r.get(spec.target) is not None and np.isfinite(r[spec.target])]
    features, dropped = infer_schema(rows, exclude=exclude, group_col=spec.group_col, time_col=spec.time_col)
    out: dict[str, Any] = {"target": spec.target, "features": [f.__dict__ for f in features], "dropped": dropped}
    if not features or len(rows) < 2:
        out["reliability"] = {"verdict": "insufficient", "reasons": ["no usable features or observations"]}
        return out

    X, vocab = encode(rows, features, spec.time_col)
    y = np.array([float(r[spec.target]) for r in rows])
    groups = np.array([str(r[spec.group_col]) for r in rows])
    times = np.array([str(r[spec.time_col]) for r in rows])
    cat_mask = np.array([f.kind == "categorical" for f in features])
    names = [f.name for f in features]
    out["profile"] = profile(X, y, features, vocab, groups, times)

    reasons = []
    n_groups, n_periods = len(np.unique(groups)), len(np.unique(times))
    if len(rows) < MIN_ROWS:
        reasons.append(f"too few observations: {len(rows)} < {MIN_ROWS}")
    if n_groups < MIN_ENTITIES:
        reasons.append(f"too few deals: {n_groups} < {MIN_ENTITIES}")
    if n_periods < MIN_PERIODS:
        reasons.append(f"too few periods: {n_periods} < {MIN_PERIODS}")
    if float(np.std(y)) == 0:
        reasons.append("target does not vary")
    if reasons:
        out["reliability"] = {"verdict": "insufficient", "reasons": reasons}
        return out

    # Grouped CV: an entity never sits in train and test at once.
    k = min(5, n_groups)
    oof_gbm, oof_lin = np.zeros_like(y), np.zeros_like(y)
    fold_importance = []
    rng = np.random.default_rng(seed)
    for train, test in GroupKFold(n_splits=k).split(X, y, groups):
        gbm = _Pipe(cat_mask, seed).fit(X[train], y[train])
        lin = _Linear(cat_mask, [len(vocab.get(n, [])) for n in names]).fit(X[train], y[train])
        oof_gbm[test], oof_lin[test] = gbm.predict(X[test]), lin.predict(X[test])
        pi = permutation_importance(gbm.model, X[test], y[test], n_repeats=4, random_state=seed, scoring=None)
        fold_importance.append(np.maximum(pi.importances_mean, 0))
    r2_gbm, r2_lin = float(r2_score(y, oof_gbm)), float(r2_score(y, oof_lin))
    out["models"] = {
        "cv_folds": k,
        "gradient_boosting_r2": r2_gbm,
        "linear_r2": r2_lin,
        "nonlinear_gain": r2_gbm - r2_lin,
    }

    final = _Pipe(cat_mask, seed).fit(X, y)
    predict = final.predict
    fi = np.vstack(fold_importance)
    mean_imp = fi.mean(axis=0)
    share = mean_imp / mean_imp.sum() if mean_imp.sum() > 0 else mean_imp
    fold_rank = [_rank_corr(fi[i], fi[j]) for i, j in combinations(range(len(fi)), 2)]

    # Sensitivity to hyperparameters and to the time window.
    def in_sample_importance(model) -> np.ndarray:
        pi = permutation_importance(model.model, X, y, n_repeats=3, random_state=seed)
        return np.maximum(pi.importances_mean, 0)

    main_in = in_sample_importance(final)
    alt = [
        in_sample_importance(_Pipe(cat_mask, seed + 1, max_leaf_nodes=7, learning_rate=0.1).fit(X, y)),
        in_sample_importance(_Pipe(cat_mask, seed + 2, max_leaf_nodes=31, min_samples_leaf=4).fit(X, y)),
    ]
    param_rank = [_rank_corr(main_in, a) for a in alt]
    period_order = sorted(np.unique(times))
    early = np.isin(times, period_order[: len(period_order) // 2 + 1])
    time_rank = None
    if early.sum() > 20 and (~early).sum() > 20:
        e = _Pipe(cat_mask, seed).fit(X[early], y[early])
        l = _Pipe(cat_mask, seed).fit(X[~early], y[~early])
        ie = np.maximum(permutation_importance(e.model, X[early], y[early], n_repeats=3, random_state=seed).importances_mean, 0)
        il = np.maximum(permutation_importance(l.model, X[~early], y[~early], n_repeats=3, random_state=seed).importances_mean, 0)
        time_rank = _rank_corr(ie, il)

    order = np.argsort(-mean_imp)
    out["importance"] = [
        {
            "feature": names[j],
            "label": labels.get(features[j].source, features[j].source),
            "kind": features[j].kind,
            "importance": float(mean_imp[j]),
            "share": float(share[j]),
            "fold_std": float(fi[:, j].std()),
            "rank": int(r + 1),
        }
        for r, j in enumerate(order)
    ]
    stability = {
        "fold_rank_corr": float(np.mean(fold_rank)) if fold_rank else None,
        "hyperparam_rank_corr": float(np.mean(param_rank)),
        "time_split_rank_corr": time_rank,
        "top3_consistency": float(np.mean([len(set(np.argsort(-f)[:3]) & set(order[:3])) / 3 for f in fi])),
    }
    out["stability"] = stability

    sub = X[rng.choice(len(X), size=min(300, len(X)), replace=False)]
    shapes = []
    for j in [j for j in order if not cat_mask[j]][:TOP_SHAPES]:
        grid = _grid(X[:, j], False)
        pd = _pd_1d(predict, sub, j, grid)
        shape = classify_shape(grid, pd, float(np.std(y)))
        shapes.append({"feature": names[j], "label": labels.get(features[j].source, features[j].source),
                       "grid": grid.tolist(), "partial_dependence": pd.tolist(), **shape})
    out["shapes"] = shapes

    top = [int(j) for j in order[:TOP_INTERACT] if mean_imp[j] > 0]
    small = X[rng.choice(len(X), size=min(70, len(X)), replace=False)]
    inter = []
    for a, b in combinations(top, 2):
        h2 = _h_statistic(predict, small, a, b)
        entry = {"features": [names[a], names[b]],
                 "labels": [labels.get(features[a].source, features[a].source),
                            labels.get(features[b].source, features[b].source)],
                 "h": float(np.sqrt(max(h2, 0)))}
        if not cat_mask[a]:
            entry["conditional"] = _conditional_effect(predict, sub, a, b, bool(cat_mask[b]))
        inter.append(entry)
    inter.sort(key=lambda e: -e["h"])
    out["interactions"] = inter

    # Local contributions for the observations with the largest |target| in the last period.
    last = times == period_order[-1]
    idx = np.where(last)[0]
    idx = idx[np.argsort(-np.abs(y[idx]))][:3]
    feats = [int(j) for j in order[:8]]
    local = []
    for i in idx:
        base, phi = _local_shapley(predict, X, X[i], feats, rng)
        local.append({
            "entity": groups[i], "period": times[i], "actual": float(y[i]),
            "predicted": float(predict(X[i : i + 1])[0]), "baseline": base,
            "contributions": [{"feature": names[f], "value": float(p)} for f, p in zip(feats, phi)],
            "unexplained": float(predict(X[i : i + 1])[0] - base - phi.sum()),
        })
    out["local"] = local

    verdict, why = "reliable", []
    if r2_gbm < GOOD_R2:
        verdict = "unreliable"
        why.append(f"low R²: {r2_gbm:.2f} < {GOOD_R2}")
    ranks = [v for v in (stability["fold_rank_corr"], stability["hyperparam_rank_corr"], time_rank) if v is not None]
    if ranks and min(ranks) < STABLE_RANK:
        verdict = "unstable" if verdict == "reliable" else verdict
        why.append(f"unstable ranking: min ρ = {min(ranks):.2f} < {STABLE_RANK}")
    out["reliability"] = {"verdict": verdict, "reasons": why}
    return out
