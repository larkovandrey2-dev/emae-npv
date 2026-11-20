"""Structured, citable facts handed to the LLM. Every number has an id and a source path."""

from __future__ import annotations

from typing import Any

from .decomposition import FACTOR_LABELS

SCENARIO_LABELS = {
    "PARALLEL_UP": "параллельный рост ставок",
    "PARALLEL_DOWN": "параллельное снижение ставок",
    "SHORT_UP": "рост коротких ставок",
    "SHORT_DOWN": "снижение коротких ставок",
    "STEEPENER": "увеличение наклона (steepener)",
    "FLATTENER": "уменьшение наклона (flattener)",
}
SHAPE_LABELS = {
    "flat": "влияние почти отсутствует",
    "linear": "близко к линейному",
    "nonlinear_monotonic": "монотонное нелинейное",
    "threshold": "пороговый эффект",
    "saturation": "насыщение",
    "acceleration": "ускорение (эффект растёт к краю диапазона)",
    "u_shape": "U-образная зависимость",
    "inverted_u": "перевёрнутая U-образная зависимость",
}


def money(v: float) -> str:
    a = abs(v)
    sign = "−" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}{a / 1e9:.2f} млрд ₽".replace(".", ",")
    if a >= 1e6:
        return f"{sign}{a / 1e6:.2f} млн ₽".replace(".", ",")
    if a >= 1e3:
        return f"{sign}{a / 1e3:.1f} тыс. ₽".replace(".", ",")
    return f"{sign}{a:.0f} ₽"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%".replace(".", ",").replace("-", "−")


def num(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}".replace(".", ",").replace("-", "−")


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
    fb.add(f"ЭКпрбк на {first['date']}", first["ec"], "RUB", money(first["ec"]), "periods[0].ec")
    fb.add(f"ЭКпрбк на {last['date']}", last["ec"], "RUB", money(last["ec"]), "periods[-1].ec")
    fb.add("Изменение ЭКпрбк за период", last["ec"] - first["ec"], "RUB", money(last["ec"] - first["ec"]), "periods")
    fb.add(f"Определяющий сценарий на {first['date']}", first["worst_scenario"], "text",
           SCENARIO_LABELS.get(first["worst_scenario"], first["worst_scenario"]), "periods[0].worst_scenario")
    fb.add(f"Определяющий сценарий на {last['date']}", last["worst_scenario"], "text",
           SCENARIO_LABELS.get(last["worst_scenario"], last["worst_scenario"]), "periods[-1].worst_scenario")
    fb.add(f"NPV портфеля на {first['date']}", first["npv_base"], "RUB", money(first["npv_base"]), "periods[0].npv_base")
    fb.add(f"NPV портфеля на {last['date']}", last["npv_base"], "RUB", money(last["npv_base"]), "periods[-1].npv_base")
    fb.add("Изменение NPV за период", last["npv_base"] - first["npv_base"], "RUB",
           money(last["npv_base"] - first["npv_base"]), "periods")
    fb.add("Число сделок в начале и конце", f"{first['deals']} → {last['deals']}", "text",
           f"{first['deals']} → {last['deals']}", "periods[].deals")

    dec = result.get("decomposition") or {}
    for metric, title in (("ec", "ЭКпрбк"), ("npv_base", "NPV")):
        m = dec.get("metrics", {}).get(metric)
        if not m:
            continue
        for c in m["contributions"]:
            label = FACTOR_LABELS[c["factor"]]
            fb.add(f"Вклад в изменение {title}: {label} (Шепли)", c["shapley"], "RUB", money(c["shapley"]),
                   f"decomposition.metrics.{metric}.contributions[{c['factor']}].shapley")
            fb.add(f"Изолированный эффект на {title}: {label} (по одному фактору)", c["one_at_a_time"], "RUB",
                   money(c["one_at_a_time"]), f"decomposition.metrics.{metric}.contributions[{c['factor']}].one_at_a_time")
        for it in m["interactions"][:2]:
            a, b = (FACTOR_LABELS[f] for f in it["factors"])
            fb.add(f"Взаимодействие для {title}: {a} × {b}", it["value"], "RUB", money(it["value"]),
                   f"decomposition.metrics.{metric}.interactions")
        fb.add(f"Необъяснённый остаток {title}", m["residual"], "RUB", money(m["residual"]),
               f"decomposition.metrics.{metric}.residual")
        fb.add(f"Доля изменения {title}, которую не объясняет сумма изолированных эффектов",
               m["linear_baseline"]["unexplained_share"], "share", pct(m["linear_baseline"]["unexplained_share"]),
               f"decomposition.metrics.{metric}.linear_baseline")

    for d in (dec.get("deals") or [])[:3]:
        fb.add(f"Изменение NPV сделки {d['deal_id']}", d["change"], "RUB", money(d["change"]), "decomposition.deals")

    model = result.get("factor_model") or {}
    if model.get("models"):
        fb.add("R² градиентного бустинга (кросс-валидация по сделкам)", model["models"]["gradient_boosting_r2"],
               "ratio", num(model["models"]["gradient_boosting_r2"]), "factor_model.models")
        fb.add("R² линейной модели (кросс-валидация по сделкам)", model["models"]["linear_r2"], "ratio",
               num(model["models"]["linear_r2"]), "factor_model.models")
    for imp in (model.get("importance") or [])[:5]:
        fb.add(f"Глобальная важность: {imp['label']}", imp["share"], "share", pct(imp["share"]),
               f"factor_model.importance[{imp['feature']}]")
    for sh in (model.get("shapes") or [])[:4]:
        desc = SHAPE_LABELS.get(sh["shape"], sh["shape"])
        fb.add(f"Форма зависимости: {sh['label']}", sh["shape"], "text", desc, f"factor_model.shapes[{sh['feature']}]")
        if "threshold" in sh:
            fb.add(f"Порог для {sh['label']}", sh["threshold"], "value", num(sh["threshold"], 3),
                   f"factor_model.shapes[{sh['feature']}].threshold")
        if "turning_point" in sh:
            fb.add(f"Точка разворота для {sh['label']}", sh["turning_point"], "value", num(sh["turning_point"], 3),
                   f"factor_model.shapes[{sh['feature']}].turning_point")
    for it in (model.get("interactions") or [])[:3]:
        a, b = it["labels"]
        fb.add(f"Сила взаимодействия (H Фридмана): {a} × {b}", it["h"], "ratio", num(it["h"]),
               "factor_model.interactions")
        cond = it.get("conditional")
        if cond:
            fb.add(f"Эффект «{a}» при низком «{b}»", cond["effect_of_a_at_b_low"], "RUB",
                   money(cond["effect_of_a_at_b_low"]), "factor_model.interactions.conditional")
            fb.add(f"Эффект «{a}» при высоком «{b}»", cond["effect_of_a_at_b_high"], "RUB",
                   money(cond["effect_of_a_at_b_high"]), "factor_model.interactions.conditional")
    st = model.get("stability") or {}
    for key, label in (("fold_rank_corr", "между фолдами"), ("hyperparam_rank_corr", "при смене гиперпараметров"),
                       ("time_split_rank_corr", "между ранними и поздними периодами")):
        if st.get(key) is not None:
            fb.add(f"Устойчивость ранжирования факторов {label} (ρ Спирмена)", st[key], "ratio", num(st[key]),
                   f"factor_model.stability.{key}")

    reliability = result.get("reliability", {})
    return {
        "question_scope": "Факторный анализ изменения ЭКпрбк и NPV банковского портфеля (IRRBB)",
        "metric_definitions": {
            "ЭКпрбк": "минимум ΔNPV по шести стресс-сценариям IRRBB; отрицательное значение — потеря",
            "Шепли": "точный вклад группы факторов; сумма вкладов равна изменению метрики",
            "по одному фактору": "эффект при изменении только этого фактора; разница с изменением — нелинейность и взаимодействия",
        },
        "facts": fb.items,
        "reliability": reliability,
        "limitations": result.get("limitations", []),
    }
