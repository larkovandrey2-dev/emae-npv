"""Run orchestration end to end with in-memory ports (no network, no database)."""

import math
from datetime import date

from factor_lab.adapters.llm.template import TemplateExplainer
from factor_lab.application.explanation import ExplainService
from factor_lab.application.pipeline import RunPipeline, RunRequest
from factor_lab.application.ports import DealValues, PortfolioValuation
from factor_lab.domain.curves import BASE, Curve, CurvePoint, ScenarioCurves

TENORS = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 15, 20]
SHOCKS = {"PARALLEL_UP": 0.04, "PARALLEL_DOWN": -0.04, "SHORT_UP": 0.03, "SHORT_DOWN": -0.03,
          "STEEPENER": 0.01, "FLATTENER": -0.01}


class Deals:
    def rows(self):
        out = []
        for i in range(12):
            out.append({"deal_id": str(i), "report_dttm": "2022-08-31", "deal_report_dttm": "2022-08-31",
                        "currency_id": "RUB", "maturity_dttm": f"{2024 + i}-08-31",
                        "current_principal_amt": str(1000 * (i + 1)), "interest_rate": str(0.05 + 0.01 * (i % 4)),
                        "discriminator": ["ANN", "RGM", "SIMPLE"][i % 3], "rate_type_cd": "FIXED",
                        "deal_dttm": "2021-01-01"})
        out.append({**out[0], "deal_id": "99", "currency_id": "USD"})
        return out

    def batches(self, size):
        rows = self.rows()
        for i in range(0, len(rows), size):
            yield rows[i:i + size]

    def summary(self):
        return {"count": len(self.rows()), "report_dates": ["2022-08-31"]}

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


class Session:
    """In-memory stand-in for the calculator portfolio: linear amortisation, simple discounting."""

    def __init__(self):
        self.deals, self.kept, self.uploads, self.closed = {}, {}, 0, False

    def upload(self, deals, currency):
        self.uploads += 1
        ok = [d for d in deals if d["currency_id"] == currency]
        self.deals.update({d["deal_id"]: d for d in ok})
        return {"accepted": len(ok), "skipped": len(deals) - len(ok), "rejected_total": 0, "rejected": [], "total": len(self.deals)}

    def seal(self, dates):
        return {"deals": len(self.deals), "valid": len(self.deals), "failed_total": 0, "failures": []}

    def _deal(self, d, as_of):
        rd, mat = date(2022, 8, 31), date.fromisoformat(d["maturity_dttm"])
        t = date.fromisoformat(as_of)
        if mat <= t:
            return None
        p0 = float(d["current_principal_amt"])
        return p0 * (mat - t).days / (mat - rd).days, (mat - t).days / 365, float(d["interest_rate"])

    def value(self, as_of, sets, sample_ids=None, only_ids=None, keep=None):
        totals = {name: {s: 0.0 for s in curves} for name, curves in sets}
        deals, kept, valued, matured = {}, {}, 0, 0
        for k, d in self.deals.items():
            if only_ids is not None and k not in only_ids:
                continue
            state = self._deal(d, as_of)
            if state is None:
                matured += 1
                continue
            p, T, coupon = state
            per_set = [{s: p * (1 + coupon * T) * (1 + c.rate_at(T)) ** (-T) for s, c in curves.items()} for _, curves in sets]
            for (name, _), npv in zip(sets, per_set):
                for s, v in npv.items():
                    totals[name][s] += v
            valued += 1
            kept[k] = per_set[0][BASE]
            if only_ids is not None or k in (sample_ids or []):
                deals[k] = DealValues(p, per_set if only_ids is not None else per_set[:1])
        if keep:
            self.kept[keep] = kept
        return PortfolioValuation(as_of, valued, matured, 0, {}, totals, deals)

    def movers(self, from_key, to_key, n):
        a, b = self.kept[from_key], self.kept[to_key]
        rows = [{"deal_id": k, "before": a.get(k, 0.0), "after": b.get(k, 0.0), "change": b.get(k, 0.0) - a.get(k, 0.0)}
                for k in set(a) | set(b)]
        return sorted(rows, key=lambda r: -abs(r["change"]))[:n]

    def close(self):
        self.closed = True


class Calc:
    def __init__(self):
        self.session = None

    def open_portfolio(self):
        self.session = Session()
        return self.session

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
    calc = Calc()
    pipe = RunPipeline(Deals(), Curves(), calc, repo, ExplainService(t, t, repo), workers=2, panel_sample=12)
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
    assert calc.session.closed, "the calculator portfolio must be released"
    assert res["source"]["loaded"] == 12  # USD deal filtered out
    assert res["factor_model"]["sample"] == {"deals": 12, "of": 12}
    assert len(res["decomposition"]["deals"]) == 10
    top = res["decomposition"]["deals"][0]
    assert abs(sum(top["contributions"].values()) - top["change"]) < 1e-6 * max(1, abs(top["change"]))


def test_factor_model_uses_a_sample_of_large_portfolios():
    repo = Repo()
    t = TemplateExplainer()
    pipe = RunPipeline(Deals(), Curves(), Calc(), repo, ExplainService(t, t, repo), workers=1, panel_sample=5)
    pipe.run("r", RunRequest(start="2022-08-31", end="2022-12-31", explain=False))
    res = repo.runs["r"]["result"]
    assert res["factor_model"]["sample"] == {"deals": 5, "of": 12}
    assert any("выборке 5 сделок из 12" in x for x in res["limitations"])
    # totals still cover the whole portfolio
    assert res["periods"][0]["deals"] == 12
