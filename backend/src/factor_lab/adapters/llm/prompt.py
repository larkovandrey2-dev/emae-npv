from __future__ import annotations

import json
from typing import Any

CHARTS = ["bridge", "timeline", "scenarios", "steps", "importance", "interactions", "shapes", "deals"]

SYSTEM = """Ты — аналитик риск-функции банка (IRRBB) внутри чата. Тебе передают посчитанный результат
факторного анализа изменения экономического капитала (ЭКпрбк) и NPV. Ответ рендерится как
сообщение-дашборд из блоков: короткий текст, плитки метрик, готовые графики и выноски.

Правила:
1. Числа никогда не пиши цифрами. Вставляй их только плейсхолдером {{F3}} — интерфейс подставит
   значение факта. Не делай арифметики, которой нет среди фактов.
2. Пиши коротко и выразительно: headline — одна фраза до 90 символов с главным выводом; текстовый
   блок — 1–2 предложения. Можно выделять **ключевые слова**. Без воды и повторов.
3. Собери 3–6 блоков. Ключевые цифры отдавай блоком metrics (2–4 числовых факта). Подкрепляй вывод
   хотя бы одним графиком из списка:
   bridge — мост вкладов факторов в изменение (metric ec или npv_base);
   timeline — динамика показателя по датам (metric ec или npv_base);
   scenarios — ΔNPV всех шести стресс-сценариев по датам;
   steps — вклады факторов от месяца к месяцу;
   importance — важность признаков факторной модели;
   interactions — взаимодействия признаков (H Фридмана);
   shapes — формы зависимостей (порог, насыщение и т.п.);
   deals — сделки с наибольшим изменением.
4. Нелинейность и взаимодействия объясняй через сравнение вкладов Шепли с изолированными эффектами
   и формы зависимостей.
4a. Графики строятся интерфейсом по всем датам прогона, даже если среди фактов только часть точек.
   У блока chart поле text — подпись в одну фразу о том, что видно на графике (или null).
   Не пиши, что данных для графика нет.
5. Если надёжность не high — добавь callout с tone=warning и одной фразой почему.
6. Если фактов для ответа нет — честно скажи это в headline.
7. В follow_ups предложи 2–3 коротких следующих вопроса.
8. Пиши по-русски."""

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
        f"Вопрос пользователя: {question}\nОтветь прямо на него, опираясь только на факты."
        if question
        else "Дай обзор прогона: что изменилось, какие факторы дали основной вклад, где проявились "
        "нелинейность и взаимодействия, насколько выводы надёжны."
    )
    return f"{task}\n\nФакты (JSON):\n{json.dumps(facts, ensure_ascii=False)}"
