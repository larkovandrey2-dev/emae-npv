"""Run storage in PostgreSQL."""

from __future__ import annotations

import json
import math
import uuid
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


def _clean(value: Any) -> Any:
    """JSON-safe copy: NaN/inf become null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


class PostgresRunRepository:
    def __init__(self, dsn: str, migrations: Path):
        self.pool = ConnectionPool(dsn, min_size=1, max_size=8, open=True, kwargs={"autocommit": True})
        self.migrate(migrations)

    def migrate(self, folder: Path) -> None:
        with self.pool.connection() as c:
            c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY)")
            done = {r[0] for r in c.execute("SELECT version FROM schema_migrations").fetchall()}
            for f in sorted(folder.glob("*.sql")):
                if f.name in done:
                    continue
                with c.transaction():
                    c.execute(f.read_text())
                    c.execute("INSERT INTO schema_migrations VALUES (%s)", (f.name,))

    def ping(self) -> bool:
        try:
            with self.pool.connection() as c:
                c.execute("SELECT 1")
            return True
        except Exception:
            return False

    def create(self, request: dict[str, Any]) -> str:
        rid = str(uuid.uuid4())
        with self.pool.connection() as c:
            c.execute("INSERT INTO runs (id, status, stage, progress, request) VALUES (%s,'running','В очереди',0,%s)",
                      (rid, Jsonb(_clean(request))))
        return rid

    def progress(self, run_id: str, stage: str, progress: float) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE runs SET stage=%s, progress=%s, updated_at=now() WHERE id=%s", (stage, progress, run_id))

    def complete(self, run_id: str, result: dict[str, Any]) -> None:
        result = _clean(result)
        with self.pool.connection() as c, c.transaction():
            c.execute("UPDATE runs SET status='done', stage='Готово', progress=1, result=%s, updated_at=now() WHERE id=%s",
                      (Jsonb(result), run_id))
            for p in result["periods"]:
                c.execute(
                    "INSERT INTO run_periods (run_id, period, curve_date, deals, npv_base, ec, worst_scenario, dnpv)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (run_id, p["date"], p["curve_date"] or None, p["deals"], p["npv_base"], p["ec"],
                     p["worst_scenario"], Jsonb(p["dnpv"])),
                )
            exp = result.get("explanation")
            if exp and "answer" in exp:
                self._insert_explanation(c, run_id, exp)

    def fail(self, run_id: str, error: str) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE runs SET status='failed', error=%s, updated_at=now() WHERE id=%s", (error[:4000], run_id))

    def _row(self, r) -> dict[str, Any]:
        return {"id": str(r[0]), "created_at": r[1].isoformat(), "updated_at": r[2].isoformat() if r[2] else None,
                "status": r[3], "stage": r[4], "progress": r[5], "request": r[6], "error": r[7]}

    def get(self, run_id: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(run_id)
        except ValueError:
            return None
        with self.pool.connection() as c:
            r = c.execute("SELECT id, created_at, updated_at, status, stage, progress, request, error, result"
                          " FROM runs WHERE id=%s", (run_id,)).fetchone()
        if not r:
            return None
        out = self._row(r)
        out["result"] = r[8]
        return out

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.pool.connection() as c:
            rows = c.execute(
                "SELECT r.id, r.created_at, r.updated_at, r.status, r.stage, r.progress, r.request, r.error,"
                " (SELECT json_agg(json_build_object('date', p.period, 'ec', p.ec, 'npv_base', p.npv_base)"
                "  ORDER BY p.period) FROM run_periods p WHERE p.run_id = r.id)"
                " FROM runs r ORDER BY r.created_at DESC LIMIT %s", (limit,)).fetchall()
        out = []
        for r in rows:
            item = self._row(r)
            item["periods"] = r[8] or []
            out.append(item)
        return out

    def _insert_explanation(self, c, run_id: str, record: dict[str, Any]) -> str:
        eid = str(uuid.uuid4())
        c.execute(
            "INSERT INTO explanations (id, run_id, question, provider, model, answer, verification, facts, note)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (eid, run_id, record.get("question"), record["provider"], record.get("model"),
             Jsonb(_clean(record["answer"])), Jsonb(_clean(record["verification"])),
             Jsonb(_clean(record.get("facts", []))), record.get("note")),
        )
        return eid

    def save_explanation(self, run_id: str, record: dict[str, Any]) -> dict[str, Any]:
        with self.pool.connection() as c:
            eid = self._insert_explanation(c, run_id, record)
        return {"id": eid, **record}

    def explanations(self, run_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as c:
            rows = c.execute(
                "SELECT id, created_at, question, provider, model, answer, verification, note"
                " FROM explanations WHERE run_id=%s ORDER BY created_at", (run_id,)).fetchall()
        return [{"id": str(r[0]), "created_at": r[1].isoformat(), "question": r[2], "provider": r[3],
                 "model": r[4], "answer": r[5], "verification": r[6], "note": r[7]} for r in rows]
