"""LLM adapter on the OpenAI Codex SDK (`openai-codex`).

Authentication is reused from CODEX_HOME/auth.json (the ChatGPT login of the host,
mounted into the container). The thread is ephemeral, read-only and denies every
tool approval: the model only reads the prompt and returns JSON by schema.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .prompt import SCHEMA, SYSTEM, user_prompt


class CodexExplainer:
    name = "codex"

    def __init__(self, model: str | None = None, effort: str | None = None, timeout_s: float = 180,
                 codex_home: str | None = None):
        self.model = model or None
        self.effort = effort or None
        self.timeout_s = timeout_s
        self.codex_home = codex_home or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
        self._lock = threading.Lock()

    def available(self) -> bool:
        return (Path(self.codex_home) / "auth.json").is_file()

    def _run(self, prompt: str) -> str:
        from openai_codex import ApprovalMode, Codex, CodexConfig, Sandbox

        workdir = tempfile.mkdtemp(prefix="codex-explain-")
        config = CodexConfig(cwd=workdir, env={"CODEX_HOME": self.codex_home},
                             client_name="factor_lab", client_title="Factor Lab explainer")
        with Codex(config) as codex:
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                sandbox=Sandbox.read_only,
                cwd=workdir,
                ephemeral=True,
                developer_instructions=SYSTEM,
                model=self.model,
            )
            kwargs: dict[str, Any] = {"output_schema": SCHEMA}
            if self.effort:
                kwargs["effort"] = self.effort
            result = thread.run(prompt, **kwargs)
            if result.final_response is None:
                raise RuntimeError(f"языковая модель не вернула ответ: {result.status}")
            return result.final_response

    def explain(self, facts: dict[str, Any], question: str | None) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError("нет авторизации языковой модели")
        box: dict[str, Any] = {}

        def target():
            try:
                with self._lock:
                    box["text"] = self._run(user_prompt(facts, question))
            except Exception as exc:  # surfaced to the caller below
                box["error"] = exc

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(self.timeout_s)
        if worker.is_alive():
            raise TimeoutError(f"языковая модель не ответила за {self.timeout_s:.0f} с")
        if "error" in box:
            raise box["error"]
        text = box["text"].strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        answer = json.loads(text)
        for key in ("headline", "blocks", "follow_ups", "confidence"):
            if key not in answer:
                raise ValueError(f"ответ модели без поля {key}")
        return answer
