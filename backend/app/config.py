from pathlib import Path
import os

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
FIXTURES = ROOT_DIR / "data" / "fixtures"
RUNTIME = Path(
    os.getenv(
        "FACTOR_LAB_RUNTIME_DIR", os.getenv("FACTOR_LAB_RUNTIME", ROOT_DIR / ".runtime")
    )
).resolve()
ENGINE_DIR = Path(os.getenv("FACTOR_LAB_ENGINE", ROOT_DIR / ".engine"))
ENGINE_BIN = Path(
    os.getenv(
        "CFCALC_BIN", ENGINE_DIR / "cashflow-rs" / "target" / "release" / "cfcalc"
    )
).resolve()
INDICATOR_FILE = Path(
    os.getenv("CFCALC_INDICATOR", ENGINE_DIR / "cashflow-rs" / "data" / "cbkey1m.csv")
).resolve()
ASSESSMENT_DATE = "2022-08-31"
PROCESS_TIMEOUT = 30
REQUEST_DEADLINE = 60
