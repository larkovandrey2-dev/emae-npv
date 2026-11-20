"""Deterministic explainer: no model, a dashboard message assembled from facts. Fallback and offline mode."""

from __future__ import annotations

from typing import Any


def _block(kind: str, text: str | None = None, fact_ids: list[str] | None = None, chart: str | None = None,
           metric: str | None = None, tone: str | None = None) -> dict[str, Any]:
    return {"type": kind, "text": text, "fact_ids": fact_ids, "chart": chart, "metric": metric, "tone": tone}


class TemplateExplainer:
    name = "template"
    model = None

    def available(self) -> bool:
        return True

    def explain(self, facts: dict[str, Any], question: str | None) -> dict[str, Any]:
        items = facts["facts"]

        def find(prefix: str) -> list[dict[str, Any]]:
            return [f for f in items if f["label"].startswith(prefix)]

        contrib = sorted(find("Вклад в изменение ЭКпрбк"), key=lambda f: -abs(f["value"]))
        blocks = [_block("metrics", fact_ids=[f["id"] for f in items[:3]])]
        if contrib:
            top = contrib[0]
            name = top["label"].split(": ", 1)[1].replace(" (Шепли)", "")
            blocks.append(_block("text", f"Главный вклад дал фактор **{name}**: {{{{{top['id']}}}}}."))
        blocks.append(_block("chart", chart="bridge", metric="ec"))
        blocks.append(_block("chart", chart="timeline", metric="ec"))
        rel = facts.get("reliability", {})
        if rel.get("confidence") != "high":
            reasons = rel.get("reasons") or ["выводы модели ограничены"]
            blocks.append(_block("callout", f"Надёжность ограничена: {reasons[0]}.", tone="warning"))
        headline = f"ЭКпрбк изменился на {{{{{items[2]['id']}}}}} за период"
        if question:
            headline = "Без языковой модели отвечаю сводкой фактов"
        return {"headline": headline, "blocks": blocks,
                "follow_ups": ["Какой фактор главный?", "Насколько можно доверять выводам?"],
                "confidence": rel.get("confidence", "low")}
