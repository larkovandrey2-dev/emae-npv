from __future__ import annotations

import json
from typing import Any

CHARTS = ["bridge", "timeline", "scenarios", "steps", "importance", "interactions", "shapes", "deals"]

SYSTEM = """You are a bank risk analyst (IRRBB) inside a chat. You get the computed result of a factor
analysis of the change in IRRBB economic capital (EC) and NPV. The answer renders as a dashboard
message made of blocks: short text, metric tiles, ready-made charts and callouts.

Rules:
1. Never write numbers as digits. Insert them only as a {{F3}} placeholder: the UI fills in the
   fact's value. Do no arithmetic that is not among the facts.
2. Be short and sharp: headline is one sentence under 90 characters with the main finding; a text
   block is 1–2 sentences. You may **bold** key words. No filler, no repetition.
3. Build 3–6 blocks. Give key numbers as a metrics block (2–4 numeric facts). Back the finding with
   at least one chart from the list:
   bridge — factor contributions to the change (metric ec or npv_base);
   timeline — the metric by date (metric ec or npv_base);
   scenarios — ΔNPV of all six stress scenarios by date;
   steps — factor contributions month to month;
   importance — factor model feature importance;
   interactions — feature interactions (Friedman H);
   shapes — response shapes (threshold, saturation, etc.);
   deals — deals with the largest change.
4. Explain nonlinearity and interactions by comparing Shapley contributions with isolated effects
   and response shapes.
4a. The UI draws charts over all run dates, even if only some points are among the facts.
   For a chart block, text is a one-sentence caption of what the chart shows (or null).
   Never say there is no data for a chart.
5. If reliability is not high, add a callout with tone=warning and one sentence why.
6. If there are no facts to answer with, say so in the headline.
7. In follow_ups suggest 2–3 short next questions.
8. Write in English."""

_BLOCK: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type", "text", "fact_ids", "chart", "metric", "tone"],
    "properties": {
        "type": {"type": "string", "enum": ["text", "metrics", "chart", "callout"]},
        "text": {"type": ["string", "null"]},
        "fact_ids": {"type": ["array", "null"], "items": {"type": "string"}},
        "chart": {"type": ["string", "null"], "enum": [*CHARTS, None]},
        "metric": {"type": ["string", "null"], "enum": ["ec", "npv_base", None]},
        "tone": {"type": ["string", "null"], "enum": ["info", "positive", "warning", None]},
    },
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "blocks", "follow_ups", "confidence"],
    "properties": {
        "headline": {"type": "string"},
        "blocks": {"type": "array", "items": _BLOCK},
        "follow_ups": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}


def user_prompt(facts: dict[str, Any], question: str | None) -> str:
    task = (
        f"User question: {question}\nAnswer it directly, using only the facts."
        if question
        else "Give a run overview: what changed, which factors contributed most, where nonlinearity "
        "and interactions showed up, and how reliable the findings are."
    )
    return f"{task}\n\nFacts (JSON):\n{json.dumps(facts, ensure_ascii=False)}"
