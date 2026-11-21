from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    calculator_url: str
    curves_url: str
    deals_source: str
    indicators_csv: str | None
    llm_provider: str  # codex | template
    llm_model: str | None
    llm_effort: str | None
    llm_timeout_s: float
    codex_home: str | None
    workers: int
    panel_sample: int
    migrations: Path

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        provider = env("LLM_PROVIDER", "codex").strip().lower()
        if provider not in ("codex", "template"):
            raise ValueError(f"LLM_PROVIDER={provider!r}: ожидается codex или template")
        return cls(
            database_url=env("DATABASE_URL", "postgresql://app:app@localhost:5433/factor_lab"),
            calculator_url=env("CALCULATOR_URL", "http://localhost:8002"),
            curves_url=env("CURVES_URL", "http://localhost:8001"),
            deals_source=env("DEALS_SOURCE", str(ROOT.parent / "data" / "deals.csv")),
            indicators_csv=env("INDICATORS_CSV", str(ROOT.parent / "data" / "indicators.csv")) or None,
            llm_provider=provider,
            llm_model=env("LLM_MODEL") or None,
            llm_effort=env("LLM_EFFORT") or None,
            llm_timeout_s=float(env("LLM_TIMEOUT_S", "180")),
            codex_home=env("CODEX_HOME") or None,
            workers=int(env("WORKERS", "2")),
            panel_sample=int(env("PANEL_SAMPLE", "5000")),
            migrations=Path(env("MIGRATIONS_DIR", str(ROOT / "migrations"))),
        )
