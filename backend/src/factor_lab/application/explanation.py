"""LLM interpretation layer with a fact check. The LLM is swappable via the Explainer port."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .facts import build_facts
from .ports import Explainer, RunRepository
from .verification import verify_text

log = logging.getLogger(__name__)


PLACEHOLDER = re.compile(r"\{\{\s*(F\d+)\s*\}\}")


def answer_texts(answer: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> list[str]:
    display = {f["id"]: f"{f['display']} [{f['id']}]" for f in facts or []}

    def expand(text: str | None) -> str:
        return PLACEHOLDER.sub(lambda m: display.get(m.group(1), f"[{m.group(1)}]"), text or "")

    if "blocks" in answer:
        texts = [expand(answer.get("headline"))]
        texts += [expand(b.get("text")) for b in answer["blocks"] if b.get("text")]
        texts += [f"[{fid}]" for b in answer["blocks"] for fid in (b.get("fact_ids") or [])]
        return texts
    texts = [answer.get("summary", "")]
    texts += [s.get("text", "") for s in answer.get("sections", [])]
    texts += list(answer.get("limitations", []))
    return texts


class ExplainService:
    def __init__(self, primary: Explainer, fallback: Explainer, repository: RunRepository):
        self.primary = primary
        self.fallback = fallback
        self.repo = repository

    def explain(self, run_id: str, result: dict[str, Any], question: str | None, persist: bool = True) -> dict[str, Any]:
        facts = build_facts(result)
        provider, model, note = self.primary.name, self.primary.model, None
        try:
            answer = self.primary.explain(facts, question)
        except Exception as exc:
            log.warning("explainer_failed provider=%s: %s", self.primary.name, exc)
            note = f"языковая модель недоступна ({exc}); ответ собран по шаблону"
            provider, model = self.fallback.name, self.fallback.model
            answer = self.fallback.explain(facts, question)
        verification = verify_text(answer_texts(answer, facts["facts"]), facts["facts"])
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "question": question,
            "provider": provider,
            "model": model,
            "note": note,
            "answer": answer,
            "verification": verification,
            "facts": facts["facts"],
        }
        if persist:
            return self.repo.save_explanation(run_id, record)
        return record
