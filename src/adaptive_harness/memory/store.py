from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStore:
    """Small reference implementation of layered memory.

    Production deployments can replace this adapter with Postgres + vector search.
    The contract intentionally separates procedural knowledge from episodic logs.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                key TEXT,
                value TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1,
                source TEXT,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
                )"""
            )

    def put(
        self,
        *,
        kind: str,
        value: str,
        key: str | None = None,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> int:
        with sqlite3.connect(self.path) as db:
            cur = db.execute(
                "INSERT INTO memories(kind,key,value,confidence,source,created_at) VALUES(?,?,?,?,?,?)",
                (kind, key, value, confidence, source, _now()),
            )
            return int(cur.lastrowid)

    def recent(self, kind: str, limit: int = 20) -> list[str]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT value FROM memories WHERE kind=? AND active=1 ORDER BY id DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [r[0] for r in reversed(rows)]

    def export_json(self) -> str:
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM memories WHERE active=1 ORDER BY id").fetchall()
        return json.dumps([dict(r) for r in rows], indent=2)
