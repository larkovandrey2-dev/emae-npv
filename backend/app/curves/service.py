import csv, json
from decimal import Decimal
from ..config import FIXTURES


def load_profiles():
    profiles = json.loads((FIXTURES / "curve-profiles.json").read_text())
    with (FIXTURES / "curves.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f, delimiter=";"))
    for p in profiles:
        p["nodes"] = []
        for r in rows:
            if (
                r["curve_type"] != p["curve_type"]
                or r["curve_date"] != p["report_date"]
                or r["currency"] != p["currency"]
            ):
                continue
            rate = Decimal(r["zero_rate_percent"])
            t = Decimal(r["tenor_days"]) / Decimal(365)
            df = (
                Decimal(1) / (Decimal(1) + rate / 100) ** t
                if p["compounding"] == "annual"
                else (-rate / 100 * t).exp()
            )
            p["nodes"].append(
                {
                    "scenario": r["scenario"],
                    "tenor_days": int(r["tenor_days"]),
                    "zero_rate_percent": format(rate, "f"),
                    "df": format(df, "f"),
                }
            )
    return profiles
