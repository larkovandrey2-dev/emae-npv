"""Deterministic explainer: no model, text assembled from facts. Fallback and offline mode."""

from __future__ import annotations

from typing import Any


class TemplateExplainer:
    name = "template"
    model = None

    def available(self) -> bool:
        return True

    def explain(self, facts: dict[str, Any], question: str | None) -> dict[str, Any]:
        items = facts["facts"]

        def ref(f):
            return f"{f['display']} [{f['id']}]"

        def find(prefix):
            return [f for f in items if f["label"].startswith(prefix)]

        sections = []
        ec = items[:3]
        summary = (f"{ec[0]['label']}: {ref(ec[0])}; {ec[1]['label']}: {ref(ec[1])}. "
                   f"Изменение — {ref(ec[2])}.")
        contrib = sorted(find("Вклад в изменение ЭКпрбк"), key=lambda f: -abs(f["value"]))
        if contrib:
            lines = [f"{f['label'].split(': ', 1)[1].replace(' (Шепли)', '')}: {ref(f)}" for f in contrib]
            sections.append({"title": "Вклад факторов в изменение ЭКпрбк", "text": "; ".join(lines) + ".",
                             "fact_ids": [f["id"] for f in contrib]})
        inter = find("Взаимодействие для ЭКпрбк") + find("Доля изменения ЭКпрбк")
        if inter:
            sections.append({"title": "Нелинейность и взаимодействия",
                             "text": "; ".join(f"{f['label']}: {ref(f)}" for f in inter) + ".",
                             "fact_ids": [f["id"] for f in inter]})
        imp = find("Глобальная важность") + find("Форма зависимости")
        if imp:
            sections.append({"title": "Глобальная важность и формы зависимостей",
                             "text": "; ".join(f"{f['label']}: {ref(f)}" for f in imp) + ".",
                             "fact_ids": [f["id"] for f in imp]})
        stab = find("R²") + find("Устойчивость")
        if stab:
            sections.append({"title": "Качество и устойчивость",
                             "text": "; ".join(f"{f['label']}: {ref(f)}" for f in stab) + ".",
                             "fact_ids": [f["id"] for f in stab]})
        rel = facts.get("reliability", {})
        limitations = list(rel.get("reasons", [])) + list(facts.get("limitations", []))
        if question:
            summary = ("Шаблонный режим не отвечает на свободные вопросы; ниже — сводка фактов. "
                       + summary)
        return {"summary": summary, "sections": sections, "limitations": limitations,
                "confidence": rel.get("confidence", "low")}
