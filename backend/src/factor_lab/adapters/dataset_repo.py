"""Storage of generated portfolios: metadata in PostgreSQL, CSV files in a folder."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

FIELDS = "id, name, created_at, updated_at, status, rows, written, size_bytes, params, path, error"


def _row(r) -> dict[str, Any]:
    return {
        "id": str(r[0]), "name": r[1], "created_at": r[2].isoformat(),
        "updated_at": r[3].isoformat() if r[3] else None, "status": r[4], "rows": r[5],
        "written": r[6], "size_bytes": r[7], "params": r[8], "path": r[9], "error": r[10],
    }


class PostgresDatasetRepository:
    def __init__(self, pool: ConnectionPool, folder: Path):
        self.pool = pool
        self.folder = folder

    def path_for(self, dataset_id: str) -> Path:
        return self.folder / f"{dataset_id}.csv"

    def create(self, name: str, rows: int, params: dict[str, Any]) -> dict[str, Any]:
        did = str(uuid.uuid4())
        path = self.path_for(did)
        with self.pool.connection() as c:
            r = c.execute(
                f"INSERT INTO datasets (id, name, status, rows, params, path) VALUES (%s,%s,'running',%s,%s,%s)"
                f" RETURNING {FIELDS}", (did, name, rows, Jsonb(params), str(path))).fetchone()
        return _row(r)

    def progress(self, dataset_id: str, written: int) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE datasets SET written=%s, updated_at=now() WHERE id=%s", (written, dataset_id))

    def complete(self, dataset_id: str, size: int) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE datasets SET status='done', written=rows, size_bytes=%s, updated_at=now() WHERE id=%s",
                      (size, dataset_id))

    def fail(self, dataset_id: str, error: str) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE datasets SET status='failed', error=%s, updated_at=now() WHERE id=%s",
                      (error[:2000], dataset_id))

    def get(self, dataset_id: str) -> dict[str, Any] | None:
        try:
            uuid.UUID(dataset_id)
        except ValueError:
            return None
        with self.pool.connection() as c:
            r = c.execute(f"SELECT {FIELDS} FROM datasets WHERE id=%s", (dataset_id,)).fetchone()
        return _row(r) if r else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.pool.connection() as c:
            rows = c.execute(f"SELECT {FIELDS} FROM datasets ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
        return [_row(r) for r in rows]

    def delete(self, dataset_id: str) -> bool:
        with self.pool.connection() as c:
            deleted = c.execute("DELETE FROM datasets WHERE id=%s AND status <> 'running'",
                                (dataset_id,)).rowcount > 0
        if deleted:
            self.path_for(dataset_id).unlink(missing_ok=True)
        return deleted

    def interrupted(self) -> None:
        """Generation does not survive a restart: mark leftovers failed."""
        with self.pool.connection() as c:
            c.execute("UPDATE datasets SET status='failed', error='generation interrupted by a service restart',"
                      " updated_at=now() WHERE status='running'")
