from __future__ import annotations

import json
from typing import Any

SYSTEM = """Ты — аналитик риск-функции банка (IRRBB). Тебе передают структурированный результат
факторного анализа изменения экономического капитала (ЭКпрбк) и NPV. Числа уже посчитаны
калькулятором и статистической моделью.

Правила:
1. Не вычисляй и не придумывай числа. Любое число в тексте бери дословно из поля display факта
   и ставь рядом ссылку на его id в квадратных скобках, например «−1,23 млн ₽ [F3]».
2. Не делай арифметику над фактами (сумм, разностей, процентов), если такого факта нет.
3. Объясняй нелинейность и взаимодействия через формы зависимостей и сравнение вкладов Шепли с
   изолированными эффектами.
4. Если reliability.confidence не high или есть reasons — явно скажи, что выводы ограничены и почему.
5. Если для ответа на вопрос фактов нет — так и скажи, не додумывай.
6. Пиши по-русски, деловым языком, коротко."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "sections", "limitations", "confidence"],
    "properties": {
        "summary": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "text", "fact_ids"],
                "properties": {
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "fact_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "limitations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
}


def user_prompt(facts: dict[str, Any], question: str | None) -> str:
    task = (
        f"Вопрос пользователя: {question}\nОтветь на него, опираясь только на факты."
        if question
        else "Сформируй объяснение: что изменилось, какие факторы дали основной вклад, где "
        "проявились нелинейность и взаимодействия, насколько выводы надёжны. 3–5 разделов."
    )
    return f"{task}\n\nФакты (JSON):\n{json.dumps(facts, ensure_ascii=False)}"
