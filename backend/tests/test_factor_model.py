"""Synthetic checks: the method must recover known non-linear forms and interactions."""

import numpy as np

from factor_lab.application.analytics.factor_model import PanelSpec, analyze_panel, classify_shape


def synthetic(n_entities=60, n_periods=8, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(n_entities):
        size = rng.uniform(1, 10)
        kind = rng.choice(["A", "B", "C"])
        for t in range(n_periods):
            level = rng.uniform(0, 1)  # threshold factor
            term = rng.uniform(0, 10)  # saturating factor
            noise_col = rng.normal()
            y = (
                8.0 * (level > 0.6)  # threshold at 0.6
                + 6.0 * (1 - np.exp(-term))  # saturation
                + 1.5 * size * (kind == "A")  # interaction size × kind
                + 0.05 * rng.normal()
            )
            rows.append({"deal_id": str(e), "period": f"2024-{t + 1:02d}-28", "level": level, "term": term,
                         "size": size, "kind": kind, "noise": noise_col, "constant": "x", "target": y})
    return rows


def test_recovers_threshold_saturation_and_interaction():
    res = analyze_panel(synthetic(), PanelSpec(target="target", exclude_prefixes=("npv_",)))
    assert res["reliability"]["verdict"] == "reliable", res["reliability"]
    assert res["models"]["gradient_boosting_r2"] > 0.9
    assert res["models"]["nonlinear_gain"] > 0.1  # linear baseline is clearly worse
    names = [i["feature"] for i in res["importance"]]
    assert names.index("noise") > names.index("level") and names.index("noise") > names.index("term")
    assert any(d["column"] == "constant" for d in res["dropped"])
    shapes = {s["feature"]: s for s in res["shapes"]}
    assert shapes["level"]["shape"] == "threshold"
    assert abs(shapes["level"]["threshold"] - 0.6) < 0.1
    assert shapes["term"]["shape"] == "saturation"
    top_pair = res["interactions"][0]["features"]
    assert set(top_pair) == {"size", "kind"}


def test_reports_insufficient_data():
    res = analyze_panel(synthetic(n_entities=4, n_periods=2), PanelSpec(target="target"))
    assert res["reliability"]["verdict"] == "insufficient"


def test_u_shape():
    x = np.linspace(-1, 1, 20)
    assert classify_shape(x, x ** 2, 1.0)["shape"] == "u_shape"
    assert classify_shape(x, 2 * x, 1.0)["shape"] == "linear"
