"""Structured, citable facts handed to the LLM. Every number has an id and a source path."""

from __future__ import annotations

from typing import Any

from .decomposition import FACTOR_LABELS

SCENARIO_LABELS = {
    "PARALLEL_UP": "Parallel up",
    "PARALLEL_DOWN": "Parallel down",
    "SHORT_UP": "Short up",
    "SHORT_DOWN": "Short down",
    "STEEPENER": "Steepener",
    "FLATTENER": "Flattener",
}
SHAPE_LABELS = {
    "flat": "No effect",
    "linear": "Linear",
    "nonlinear_monotonic": "Monotonic",
    "threshold": "Threshold",
    "saturation": "Saturation",
    "acceleration": "Acceleration",
    "u_shape": "U-shape",
    "inverted_u": "Inverted U",
}


def money(v: float) -> str:
    a = abs(v)
    sign = "−" if v < 0 else ""
    for scale, unit, digits in ((1e9, "B", 2), (1e6, "M", 2), (1e3, "K", 1)):
        if a >= scale:
            return f"{sign}{a / scale:,.{digits}f}{unit} ₽"
    return f"{sign}{a:,.0f} ₽"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace("-", "−")


def num(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}".replace("-", "−")


class FactBook:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, label: str, value: float | str | None, unit: str, display: str, source: str) -> str:
        fid = f"F{len(self.items) + 1}"
        self.items.append({"id": fid, "label": label, "value": value, "unit": unit,
                           "display": display, "source": source})
        return fid


def build_facts(result: dict[str, Any]) -> dict[str, Any]:
    fb = FactBook()
    periods = result["periods"]
    first, last = periods[0], periods[-1]
    fb.add(f"EC on {first['date']}", first["ec"], "RUB", money(first["ec"]), "periods[0].ec")
    fb.add(f"EC on {last['date']}", last["ec"], "RUB", money(last["ec"]), "periods[-1].ec")
    fb.add("EC change", last["ec"] - first["ec"], "RUB", money(last["ec"] - first["ec"]), "periods")
    fb.add(f"Worst scenario on {first['date']}", first["worst_scenario"], "text",
           SCENARIO_LABELS.get(first["worst_scenario"], first["worst_scenario"]), "periods[0].worst_scenario")
    fb.add(f"Worst scenario on {last['date']}", last["worst_scenario"], "text",
           SCENARIO_LABELS.get(last["worst_scenario"], last["worst_scenario"]), "periods[-1].worst_scenario")
    fb.add(f"NPV on {first['date']}", first["npv_base"], "RUB", money(first["npv_base"]), "periods[0].npv_base")
    fb.add(f"NPV on {last['date']}", last["npv_base"], "RUB", money(last["npv_base"]), "periods[-1].npv_base")
    fb.add("NPV change", last["npv_base"] - first["npv_base"], "RUB",
           money(last["npv_base"] - first["npv_base"]), "periods")
    fb.add("Deals, start → end", f"{first['deals']} → {last['deals']}", "text",
           f"{first['deals']} → {last['deals']}", "periods[].deals")

    dec = result.get("decomposition") or {}
    for metric, title in (("ec", "EC"), ("npv_base", "NPV")):
        m = dec.get("metrics", {}).get(metric)
        if not m:
            continue
        for c in m["contributions"]:
            label = FACTOR_LABELS[c["factor"]]
            fb.add(f"{title} Shapley: {label}", c["shapley"], "RUB", money(c["shapley"]),
                   f"decomposition.metrics.{metric}.contributions[{c['factor']}].shapley")
            fb.add(f"{title} isolated: {label}", c["one_at_a_time"], "RUB",
                   money(c["one_at_a_time"]), f"decomposition.metrics.{metric}.contributions[{c['factor']}].one_at_a_time")
        for it in m["interactions"][:2]:
            a, b = (FACTOR_LABELS[f] for f in it["factors"])
            fb.add(f"{title} interaction: {a} × {b}", it["value"], "RUB", money(it["value"]),
                   f"decomposition.metrics.{metric}.interactions")
        fb.add(f"{title} residual", m["residual"], "RUB", money(m["residual"]),
               f"decomposition.metrics.{metric}.residual")
        fb.add(f"{title} nonlinear share",
               m["linear_baseline"]["unexplained_share"], "share", pct(m["linear_baseline"]["unexplained_share"]),
               f"decomposition.metrics.{metric}.linear_baseline")

    for d in (dec.get("deals") or [])[:3]:
        fb.add(f"Deal {d['deal_id']} NPV change", d["change"], "RUB", money(d["change"]), "decomposition.deals")

    model = result.get("factor_model") or {}
    if model.get("models"):
        fb.add("Boosting R² (CV)", model["models"]["gradient_boosting_r2"],
               "ratio", num(model["models"]["gradient_boosting_r2"]), "factor_model.models")
        fb.add("Linear R² (CV)", model["models"]["linear_r2"], "ratio",
               num(model["models"]["linear_r2"]), "factor_model.models")
    for imp in (model.get("importance") or [])[:5]:
        fb.add(f"Importance: {imp['label']}", imp["share"], "share", pct(imp["share"]),
               f"factor_model.importance[{imp['feature']}]")
    for sh in (model.get("shapes") or [])[:4]:
        desc = SHAPE_LABELS.get(sh["shape"], sh["shape"])
        fb.add(f"Shape: {sh['label']}", sh["shape"], "text", desc, f"factor_model.shapes[{sh['feature']}]")
        if "threshold" in sh:
            fb.add(f"Threshold: {sh['label']}", sh["threshold"], "value", num(sh["threshold"], 3),
                   f"factor_model.shapes[{sh['feature']}].threshold")
        if "turning_point" in sh:
            fb.add(f"Turning point: {sh['label']}", sh["turning_point"], "value", num(sh["turning_point"], 3),
                   f"factor_model.shapes[{sh['feature']}].turning_point")
    for it in (model.get("interactions") or [])[:3]:
        a, b = it["labels"]
        fb.add(f"Interaction H: {a} × {b}", it["h"], "ratio", num(it["h"]),
               "factor_model.interactions")
        cond = it.get("conditional")
        if cond:
            fb.add(f"Effect of {a} at low {b}", cond["effect_of_a_at_b_low"], "RUB",
                   money(cond["effect_of_a_at_b_low"]), "factor_model.interactions.conditional")
            fb.add(f"Effect of {a} at high {b}", cond["effect_of_a_at_b_high"], "RUB",
                   money(cond["effect_of_a_at_b_high"]), "factor_model.interactions.conditional")
    st = model.get("stability") or {}
    for key, label in (("fold_rank_corr", "across folds"), ("hyperparam_rank_corr", "across hyperparameters"),
                       ("time_split_rank_corr", "early vs late periods")):
        if st.get(key) is not None:
            fb.add(f"Rank stability {label} (Spearman ρ)", st[key], "ratio", num(st[key]),
                   f"factor_model.stability.{key}")

    periods = result["periods"]
    for i, p in enumerate(periods[1:-1], start=1):
        fb.add(f"EC on {p['date']}", p["ec"], "RUB", money(p["ec"]), f"periods[{i}].ec")
    for step in result.get("steps", []):
        fb.add(f"EC change {step['from']} → {step['to']}", step["ec"]["change"], "RUB",
               money(step["ec"]["change"]), "steps.ec.change")

    reliability = result.get("reliability", {})
    return {
        "question_scope": "Factor analysis of the change in IRRBB economic capital (EC) and NPV of a bank book",
        "metric_definitions": {
            "EC": "minimum ΔNPV over the six IRRBB stress scenarios; negative means a loss",
            "Shapley": "exact contribution of a factor group; contributions add up to the metric change",
            "isolated": "effect of changing only this factor; the gap to the change is nonlinearity and interactions",
        },
        "facts": fb.items,
        "reliability": reliability,
        "limitations": result.get("limitations", []),
    }
