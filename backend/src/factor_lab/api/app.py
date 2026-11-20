"""HTTP API (FastAPI). Handlers map payloads to use cases; no business logic here."""

from __future__ import annotations

import csv
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from ..application.analytics.panel import TARGETS
from ..application.pipeline import RunRequest
from ..config import Settings
from ..container import Container, build
from .schemas import QuestionCreate, RunCreate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def create_app(container: Container | None = None) -> FastAPI:
    runner = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.c = container or build(Settings.from_env())
        yield
        runner.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Factor Lab — оркестратор и факторный анализ ЭКпрбк", version="2.0.0", lifespan=lifespan)

    def c() -> Container:
        return app.state.c

    @app.get("/api/v1/health")
    def health():
        cont = c()
        services = {
            "database": cont.repository.ping(),
            "calculator": cont.calculator.health(),
            "curves": cont.curves.health(),
            "llm": cont.primary_explainer.available(),
        }
        return {"status": "ok" if all(v for k, v in services.items() if k != "llm") else "degraded",
                "services": services,
                "llm": {"provider": cont.primary_explainer.name, "model": cont.primary_explainer.model}}

    @app.get("/api/v1/deals")
    def deals():
        cont = c()
        rows = cont.deals.load()
        return {"source": cont.deals.describe(), "count": len(rows),
                "report_dates": sorted({r.get("report_dttm", "")[:10] for r in rows}),
                "deals": [{k: r.get(k) for k in ("deal_id", "currency_id", "discriminator", "rate_type_cd",
                                                 "current_principal_amt", "interest_rate", "maturity_dttm", "product")}
                          for r in rows]}

    @app.get("/api/v1/targets")
    def targets():
        return [{"id": k, "label": v} for k, v in TARGETS.items()]

    @app.post("/api/v1/runs", status_code=202)
    def create_run(body: RunCreate):
        cont = c()
        req = RunRequest(
            start=body.start.isoformat() if body.start else None,
            end=body.end.isoformat() if body.end else None,
            frequency=body.frequency,
            dates=[d.isoformat() for d in body.dates],
            target=body.target,
            explain=body.explain,
        )
        report = sorted({r["report_dttm"][:10] for r in cont.deals.load()})[0]
        try:
            req_periods = cont.pipeline.periods(req, report)
        except ValueError as exc:
            raise HTTPException(422, detail=str(exc)) from exc
        run_id = cont.repository.create({**req.to_dict(), "periods": req_periods})
        runner.submit(cont.pipeline.run, run_id, req)
        return {"id": run_id, "status": "running", "periods": req_periods}

    @app.get("/api/v1/runs")
    def list_runs(limit: int = 50):
        return c().repository.list(min(max(limit, 1), 200))

    def _run(run_id: str) -> dict:
        run = c().repository.get(run_id)
        if not run:
            raise HTTPException(404, detail="run not found")
        return run

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        run = _run(run_id)
        run["explanations"] = c().repository.explanations(run_id)
        return run

    @app.post("/api/v1/runs/{run_id}/explanations")
    async def ask(run_id: str, body: QuestionCreate):
        run = _run(run_id)
        if run["status"] != "done":
            raise HTTPException(409, detail="run is not finished")
        return await run_in_threadpool(c().explain.explain, run_id, run["result"], body.question)

    @app.get("/api/v1/runs/{run_id}/export.csv")
    def export(run_id: str):
        run = _run(run_id)
        if run["status"] != "done":
            raise HTTPException(409, detail="run is not finished")
        res = run["result"]
        out = io.StringIO()
        w = csv.writer(out, delimiter=";")
        scen = res["scenarios"]
        w.writerow(["period", "curve_date", "deals", "npv_base", "ec", "worst_scenario", *[f"dnpv_{s}" for s in scen]])
        for p in res["periods"]:
            w.writerow([p["date"], p["curve_date"], p["deals"], f"{p['npv_base']:.2f}", f"{p['ec']:.2f}",
                        p["worst_scenario"], *[f"{p['dnpv'][s]:.2f}" for s in scen]])
        w.writerow([])
        w.writerow(["factor", "ec_shapley", "ec_one_at_a_time", "npv_shapley", "npv_one_at_a_time"])
        m = res["decomposition"]["metrics"]
        for a, b in zip(m["ec"]["contributions"], m["npv_base"]["contributions"]):
            w.writerow([a["factor"], f"{a['shapley']:.2f}", f"{a['one_at_a_time']:.2f}",
                        f"{b['shapley']:.2f}", f"{b['one_at_a_time']:.2f}"])
        return Response(out.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="ec-factor-{run_id[:8]}.csv"'})

    @app.exception_handler(ValueError)
    async def value_error(_, exc: ValueError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


app = create_app()
