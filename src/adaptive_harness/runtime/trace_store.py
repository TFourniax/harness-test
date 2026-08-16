from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from adaptive_harness.contracts import ApprovalRequest, TraceEvent


class TraceStore:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, created_at TEXT, payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS approvals (id TEXT PRIMARY KEY, run_id TEXT, status TEXT, fingerprint TEXT, payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS executions (fingerprint TEXT PRIMARY KEY, run_id TEXT, tool_name TEXT, status TEXT, call_payload TEXT, observation TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS channel_cursors (channel TEXT PRIMARY KEY, next_update_id INTEGER NOT NULL)"
            )

    def append(self, event: TraceEvent) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (
                    event.id,
                    event.run_id,
                    event.kind,
                    event.created_at.isoformat(),
                    json.dumps(event.payload, default=str),
                ),
            )

    def save_approval(self, approval: ApprovalRequest) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?)",
                (
                    approval.id,
                    approval.run_id,
                    approval.status,
                    approval.fingerprint,
                    approval.model_dump_json(),
                ),
            )

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT status, payload FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
        if not row:
            return None
        status, payload = row
        obj = ApprovalRequest.model_validate_json(payload)
        obj.status = status
        return obj

    def set_approval(self, approval_id: str, status: str) -> None:
        if status not in {"approved", "rejected"}:
            raise ValueError("invalid approval status")
        with sqlite3.connect(self.path) as db:
            cur = db.execute("UPDATE approvals SET status=? WHERE id=?", (status, approval_id))
            if cur.rowcount == 0:
                raise KeyError(f"unknown approval: {approval_id}")

    def save_checkpoint(self, run_id: str, payload: dict) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO checkpoints(run_id,payload) VALUES(?,?)",
                (run_id, json.dumps(payload, default=str)),
            )

    def load_checkpoint(self, run_id: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT payload FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def clear_checkpoint(self, run_id: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM checkpoints WHERE run_id=?", (run_id,))

    def get_execution(self, fingerprint: str) -> dict | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT run_id,tool_name,status,call_payload,observation FROM executions WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
        if not row:
            return None
        run_id, tool_name, status, call_payload, observation = row
        return {
            "run_id": run_id,
            "tool_name": tool_name,
            "status": status,
            "call": json.loads(call_payload),
            "observation": json.loads(observation) if observation else None,
        }

    def begin_execution(self, fingerprint: str, run_id: str, tool_name: str, call_payload: dict) -> bool:
        """Atomically claim a non-idempotent action. False means it already exists."""
        with sqlite3.connect(self.path) as db:
            try:
                db.execute(
                    "INSERT INTO executions(fingerprint,run_id,tool_name,status,call_payload,observation) VALUES(?,?,?,?,?,NULL)",
                    (fingerprint, run_id, tool_name, "started", json.dumps(call_payload, default=str)),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def complete_execution(self, fingerprint: str, observation: dict) -> None:
        with sqlite3.connect(self.path) as db:
            cur = db.execute(
                "UPDATE executions SET status='completed', observation=? WHERE fingerprint=?",
                (json.dumps(observation, default=str), fingerprint),
            )
            if cur.rowcount == 0:
                raise KeyError(f"unknown execution fingerprint: {fingerprint}")


    def recent_run_ids(self, limit: int = 50) -> list[str]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT run_id, MAX(created_at) AS last_at FROM events "
                "GROUP BY run_id ORDER BY last_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [row[0] for row in rows]

    def events(self, run_id: str) -> list[dict]:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                "SELECT kind, created_at, payload FROM events WHERE run_id=? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [
            {"kind": kind, "created_at": created_at, "payload": json.loads(payload)}
            for kind, created_at, payload in rows
        ]

    def get_channel_cursor(self, channel: str) -> int | None:
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT next_update_id FROM channel_cursors WHERE channel=?", (channel,)
            ).fetchone()
        return int(row[0]) if row else None

    def set_channel_cursor(self, channel: str, next_update_id: int) -> None:
        if next_update_id < 0:
            raise ValueError("channel cursor cannot be negative")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO channel_cursors(channel,next_update_id) VALUES(?,?)",
                (channel, next_update_id),
            )
