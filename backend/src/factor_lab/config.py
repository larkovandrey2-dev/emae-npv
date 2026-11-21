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
    workers: int
    panel_sample: int
    migrations: Path
    datasets_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            database_url=env("DATABASE_URL", "postgresql://app:app@localhost:5433/factor_lab"),
            calculator_url=env("CALCULATOR_URL", "http://localhost:8002"),
            curves_url=env("CURVES_URL", "http://localhost:8001"),
            deals_source=env("DEALS_SOURCE", str(ROOT.parent / "data" / "deals.csv")),
            indicators_csv=env("INDICATORS_CSV", str(ROOT.parent / "data" / "indicators.csv")) or None,
            workers=int(env("WORKERS", "2")),
            panel_sample=int(env("PANEL_SAMPLE", "5000")),
            migrations=Path(env("MIGRATIONS_DIR", str(ROOT / "migrations"))),
            datasets_dir=Path(env("DATASETS_DIR", str(ROOT.parent / "data" / "datasets"))),
        )
