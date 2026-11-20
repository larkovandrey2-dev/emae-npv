from __future__ import annotations
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .config import ROOT_DIR, REQUEST_DEADLINE
from .domain import DealCreate, ValuationRequest, ExperimentRequest, InteractionRequest, DiscountExperimentRequest
from .catalog.service import Catalog
from .curves.service import load_profiles
from .integrations.cashflow_rs import CashflowEngine, EngineError, EngineTimeout
from .analytics.service import value_snapshot, run_experiment, run_interaction, run_discount_experiment, InputError
from .storage.repository import Repository
from .api.export import experiment_csv

app = FastAPI(title="Factor Lab API", version="1.0.0")
catalog = Catalog()
profiles = load_profiles()
engine = CashflowEngine()
repository = Repository()


def field_error(field, message):
    return HTTPException(
        422, detail=[{"loc": ["body", field], "msg": message, "type": "value_error"}]
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    errors = []
    for e in exc.errors():
        errors.append({"loc": list(e["loc"]), "msg": e["msg"], "type": "value_error"})
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(EngineError)
async def engine_error(request, exc):
    return JSONResponse(
        status_code=504 if isinstance(exc, EngineTimeout) else 502,
        content={"detail": str(exc), "technical": getattr(exc, "technical", None)},
    )


@app.get("/api/health")
def health():
    m = engine.metadata()
    return {
        "status": "ok" if m["ready"] else "unavailable",
        "engine_ready": m["ready"],
        "engine": {
            "commit": m["commit"],
            "sha256": m["sha256"],
            "version": m["version"],
        },
    }


@app.get("/api/deals")
def deals():
    return catalog.list()


@app.post("/api/deals", status_code=201)
def create_deal(req: DealCreate):
    return catalog.create(req)


@app.get("/api/curve-profiles")
def curve_profiles():
    return profiles


@app.post("/api/valuations")
async def valuation(req: ValuationRequest):
    if len(set(req.deal_ids)) != len(req.deal_ids):
        raise field_error("deal_ids", "ID сделок должны быть уникальными")
    snap = []
    for ident in req.deal_ids:
        d = catalog.get(ident)
        if not d:
            raise HTTPException(404, f"Сделка {ident} не найдена")
        if not d["eligible"]:
            raise field_error(
                "deal_ids", f"Сделка {ident} недоступна: {', '.join(d['restrictions'])}"
            )
        snap.append(d)
    profile = next((p for p in profiles if p["id"] == req.profile_id), None)
    if not profile:
        raise HTTPException(404, "Профиль кривой не найден")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                value_snapshot, snap, profile, req.act_act_as_365, engine
            ),
            timeout=REQUEST_DEADLINE,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Превышено общее время запроса")
    except InputError as e:
        raise field_error(e.field, str(e))
    except ValueError as e:
        raise field_error("deal_ids", str(e))


@app.post("/api/experiments")
async def experiments(req: ExperimentRequest):
    profile = next((p for p in profiles if p["id"] == req.profile_id), None)
    if not profile:
        raise HTTPException(404, "Профиль кривой не найден")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                run_experiment, req, catalog, profile, engine, repository
            ),
            timeout=REQUEST_DEADLINE,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except InputError as e:
        raise field_error(e.field, str(e))
    except ValueError as e:
        raise field_error("values", str(e))
    except asyncio.TimeoutError:
        raise HTTPException(504, "Превышено общее время запроса")


@app.post("/api/interactions")
async def interactions(req: InteractionRequest):
    profile = next((p for p in profiles if p["id"] == req.profile_id), None)
    if not profile:
        raise HTTPException(404, "Профиль кривой не найден")
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_interaction, req, catalog, profile, engine),
            timeout=REQUEST_DEADLINE,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except InputError as e:
        raise field_error(e.field, str(e))
    except ValueError as e:
        raise field_error("values", str(e))
    except asyncio.TimeoutError:
        raise HTTPException(504, "Превышено общее время запроса")


@app.post("/api/discount-experiments")
async def discount_experiments(req: DiscountExperimentRequest):
    profile = next((p for p in profiles if p["id"] == req.profile_id), None)
    if not profile:
        raise HTTPException(404, "Профиль кривой не найден")
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(run_discount_experiment, req, catalog, profile, engine),
            timeout=REQUEST_DEADLINE,
        )
        return result
    except KeyError as e:
        raise HTTPException(404, str(e))
    except InputError as e:
        raise field_error(e.field, str(e))
    except EngineError:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(504, "Превышено общее время запроса")


@app.get("/api/experiments")
def history():
    return repository.list_experiments()


@app.get("/api/experiments/{ident}")
def get_experiment(ident: str):
    e = repository.get_experiment(ident)
    if not e:
        raise HTTPException(404, "Эксперимент не найден")
    return e


@app.get("/api/experiments/{ident}/export.csv")
def export(ident: str):
    e = repository.get_experiment(ident)
    if not e:
        raise HTTPException(404, "Эксперимент не найден")
    return Response(
        experiment_csv(e),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="factor-lab-{ident}.csv"'
        },
    )


# Serve built UI when available; API remains available without frontend build.
frontend = ROOT_DIR / "frontend" / "dist"
if frontend.is_dir():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
