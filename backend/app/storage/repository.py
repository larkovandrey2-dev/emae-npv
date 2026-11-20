import sqlite3, json, os, tempfile
from ..config import RUNTIME


class Repository:
    def __init__(self, runtime=None):
        root = runtime or RUNTIME
        root.mkdir(parents=True, exist_ok=True)
        self.db = root / "factor-lab.sqlite3"
        self.runs = root / "runs"
        self.runs.mkdir(exist_ok=True)
        with self.connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, created_at TEXT NOT NULL, target_deal_id TEXT NOT NULL, target_name TEXT NOT NULL, factor TEXT NOT NULL, values_json TEXT NOT NULL, profile_name TEXT NOT NULL, payload TEXT NOT NULL)"
            )

    def connect(self):
        c = sqlite3.connect(self.db, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def save_experiment(self, e):
        payload = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
        target = next(
            d for d in e["original"]["snapshot"] if d["id"] == e["target_deal_id"]
        )
        path = self.runs / f"{e['id']}.json"
        fd, tmp = tempfile.mkstemp(prefix="run-", dir=self.runs, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        with self.connect() as c:
            c.execute(
                "INSERT INTO experiments VALUES(?,?,?,?,?,?,?,?)",
                (
                    e["id"],
                    e["created_at"],
                    e["target_deal_id"],
                    target["name"],
                    e["factor"],
                    json.dumps(e["values"]),
                    e["original"]["profile"]["name"],
                    payload,
                ),
            )

    def get_experiment(self, ident):
        with self.connect() as c:
            row = c.execute(
                "SELECT payload FROM experiments WHERE id=?", (ident,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_experiments(self):
        with self.connect() as c:
            rows = c.execute(
                "SELECT id,created_at,target_deal_id,target_name,factor,values_json,profile_name FROM experiments ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "status": "complete",
                "target_deal_id": r["target_deal_id"],
                "target_name": r["target_name"],
                "factor": r["factor"],
                "values": json.loads(r["values_json"]),
                "profile_name": r["profile_name"],
            }
            for r in rows
        ]
