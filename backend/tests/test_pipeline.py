"""Run orchestration end to end with in-memory ports (no network, no database)."""

import math
from datetime import date

from factor_lab.adapters.llm.template import TemplateExplainer
from factor_lab.application.explanation import ExplainService
from factor_lab.application.pipeline import RunPipeline, RunRequest
from factor_lab.application.ports import CalculatorResult
from factor_lab.domain.curves import Curve, CurvePoint, ScenarioCurves
from factor_lab.domain.metrics import Valuation
from factor_lab.domain.portfolio import ScheduledFlow

TENORS = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20]
SHOCKS = {"PARALLEL_UP": 0.04, "PARALLEL_DOWN": -0.04, "SHORT_UP": 0.03, "SHORT_DOWN": -0.03,
          "STEEPENER": 0.01, "FLATTENER": -0.01}


class Deals:
    def load(self):
        out = []
        for i in range(12):
            out.append({"deal_id": str(i), "report_dttm": "2022-08-31", "deal_report_dttm": "2022-08-31",
                        "currency_id": "RUB", "maturity_dttm": f"{2024 + i}-08-31",
                        "current_principal_amt": str(1000 * (i + 1)), "interest_rate": str(0.05 + 0.01 * (i % 4)),
                        "discriminator": ["ANN", "RGM", "SIMPLE"][i % 3], "rate_type_cd": "FIXED",
                        "deal_dttm": "2021-01-01", "principal_payment": "", "interest_payment": ""})
        return out

    def describe(self):
        return "memory"


class Curves:
    def _curve(self, d: str, shift: float) -> Curve:
        month = (date.fromisoformat(d) - date(2022, 8, 31)).days / 30
        return Curve(d, tuple(CurvePoint(t, 0.08 + 0.002 * month + 0.003 * math.log1p(t) + shift) for t in TENORS))

    def snapshot(self, on_or_before):
        return self.shock(self._curve(on_or_before, 0))

    def shock(self, base):
        return ScenarioCurves(base, {k: Curve(base.trade_date, tuple(CurvePoint(p.tenor_years, p.rate + (v if k != "STEEPENER" else v * p.tenor_years / 10)) for p in base.points)) for k, v in SHOCKS.items()})

    def health(self):
        return True


class Calc:
    def value(self, deals, curves, report_date, include_cashflows=False):
        rd = date.fromisoformat(report_date)
        deal_npv, schedules = {}, {}
        for d in deals:
            p = float(d["current_principal_amt"])
            T = (date.fromisoformat(d["maturity_dttm"]) - rd).days / 365
            coupon = float(d["interest_rate"])
            deal_npv[d["deal_id"]] = {s: p * (1 + coupon * T) * (1 + c.rate_at(T)) ** (-T) for s, c in curves.items()}
            schedules[d["deal_id"]] = [ScheduledFlow(f"{rd.year + k}-08-31", p / max(T, 1), max(0.0, p - p * k / max(T, 1))) for k in range(1, int(T) + 2)]
        npv = {s: sum(v[s] for v in deal_npv.values()) for s in curves}
        return CalculatorResult(Valuation(report_date, report_date, npv, deal_npv, {}), schedules if include_cashflows else {})

    def health(self):
        return True


class Repo:
    def __init__(self):
        self.runs, self.saved = {}, []

    def create(self, request):
        self.runs["r"] = {"status": "running"}
        return "r"

    def progress(self, run_id, stage, progress):
        self.runs.setdefault(run_id, {})["stage"] = stage

    def complete(self, run_id, result):
        self.runs[run_id] = {"status": "done", "result": result}

    def fail(self, run_id, error):
        self.runs[run_id] = {"status": "failed", "error": error}

    def save_explanation(self, run_id, record):
        self.saved.append(record)
        return record


def test_pipeline_end_to_end():
    repo = Repo()
    t = TemplateExplainer()
    pipe = RunPipeline(Deals(), Curves(), Calc(), repo, ExplainService(t, t, repo), workers=2)
    pipe.run("r", RunRequest(start="2022-08-31", end="2023-08-31"))
    run = repo.runs["r"]
    assert run["status"] == "done", run.get("error")
    res = run["result"]
    assert len(res["periods"]) == 13
    for metric in ("ec", "npv_base"):
        m = res["decomposition"]["metrics"][metric]
        assert abs(m["residual"]) < 1e-6 * max(1, abs(m["change"]))
    assert len(res["steps"]) == 12
    assert res["factor_model"]["reliability"]["verdict"] in ("reliable", "unstable", "unreliable")
    assert res["explanation"]["verification"]["status"] == "ok"
