from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from adaptive_harness.orchestration.contracts import VerificationCertificate, VerificationVerdict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MissionStatus(str, Enum):
    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


class TaskStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"


class FactStatus(str, Enum):
    SUPPORTED = "supported"
    VERIFIED = "verified"
    REFUTED = "refuted"


class RevisionConflict(RuntimeError):
    pass


class MissionTask(BaseModel):
    id: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    profile: str | None = None
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    status: TaskStatus = TaskStatus.PENDING
    note: str = ""
    output_summary: str = ""
    verification: VerificationCertificate | None = None
    created_at: str = ""
    updated_at: str = ""


class MissionFact(BaseModel):
    id: str
    claim: str
    evidence_refs: list[str] = Field(default_factory=list)
    source_task_id: str | None = None
    status: FactStatus = FactStatus.SUPPORTED
    evidence_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: str = ""


class MissionSnapshot(BaseModel):
    id: str
    external_key: str | None = None
    goal: str
    success_criteria: list[str] = Field(default_factory=list)
    status: MissionStatus = MissionStatus.ACTIVE
    revision: int = 0
    tasks: list[MissionTask] = Field(default_factory=list)
    facts: list[MissionFact] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class StateAuditIssue(BaseModel):
    severity: str
    code: str
    message: str
    task_id: str | None = None


class TaskStateStore:
    """SQLite canonical state plane with optimistic revisions and append-only events."""

    def __init__(self, path: str | Path, *, done_evidence_strength: float = 0.60) -> None:
        self.path = str(path)
        self.done_evidence_strength = float(done_evidence_strength)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS missions (
                    id TEXT PRIMARY KEY, external_key TEXT UNIQUE, goal TEXT NOT NULL,
                    success_criteria TEXT NOT NULL, status TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS mission_tasks (
                    mission_id TEXT NOT NULL, task_id TEXT NOT NULL, description TEXT NOT NULL,
                    dependencies TEXT NOT NULL, success_criteria TEXT NOT NULL, profile TEXT,
                    priority REAL NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL,
                    output_summary TEXT NOT NULL, verification_json TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(mission_id, task_id)
                );
                CREATE TABLE IF NOT EXISTS mission_facts (
                    mission_id TEXT NOT NULL, fact_id TEXT NOT NULL, claim TEXT NOT NULL,
                    evidence_refs TEXT NOT NULL, source_task_id TEXT, status TEXT NOT NULL,
                    evidence_strength REAL NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(mission_id, fact_id)
                );
                CREATE TABLE IF NOT EXISTS mission_events (
                    id TEXT PRIMARY KEY, mission_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(mission_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_mission_tasks_status ON mission_tasks(mission_id, status);
                CREATE INDEX IF NOT EXISTS idx_mission_facts_mission ON mission_facts(mission_id, created_at);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _loads(value: str | None, default: Any) -> Any:
        return json.loads(value) if value else default

    def _current_revision(self, db: sqlite3.Connection, mission_id: str) -> int:
        row = db.execute("SELECT revision FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not row:
            raise KeyError(f"unknown mission: {mission_id}")
        return int(row[0])

    def _check_revision(self, db: sqlite3.Connection, mission_id: str, expected_revision: int | None) -> int:
        current = self._current_revision(db, mission_id)
        if expected_revision is not None and current != int(expected_revision):
            raise RevisionConflict(
                f"stale mission revision: expected {expected_revision}, current {current}; reload before writing"
            )
        return current

    def _bump(self, db: sqlite3.Connection, mission_id: str, current_revision: int, kind: str, payload: dict[str, Any]) -> int:
        next_revision = current_revision + 1
        now = _now()
        updated = db.execute(
            "UPDATE missions SET revision=?, updated_at=? WHERE id=? AND revision=?",
            (next_revision, now, mission_id, current_revision),
        )
        if updated.rowcount != 1:
            raise RevisionConflict("mission changed concurrently; reload before writing")
        db.execute(
            "INSERT INTO mission_events(id,mission_id,revision,kind,payload,created_at) VALUES(?,?,?,?,?,?)",
            (str(uuid4()), mission_id, next_revision, kind, json.dumps(payload, default=str), now),
        )
        return next_revision

    def create_mission(self, *, goal: str, success_criteria: list[str] | None = None, external_key: str | None = None) -> MissionSnapshot:
        if not goal.strip():
            raise ValueError("mission goal cannot be empty")
        if external_key:
            with self._connect() as db:
                row = db.execute("SELECT id FROM missions WHERE external_key=?", (external_key,)).fetchone()
            if row:
                return self.snapshot(str(row[0]))
        mission_id = str(uuid4())
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO missions(id,external_key,goal,success_criteria,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,0,?,?)",
                (mission_id, external_key, goal.strip(), json.dumps(success_criteria or [], ensure_ascii=False), MissionStatus.ACTIVE.value, now, now),
            )
            db.execute(
                "INSERT INTO mission_events(id,mission_id,revision,kind,payload,created_at) VALUES(?,?,?,?,?,?)",
                (str(uuid4()), mission_id, 0, "mission_created", json.dumps({"goal": goal, "success_criteria": success_criteria or []}), now),
            )
            db.commit()
        return self.snapshot(mission_id)

    def snapshot(self, mission_id: str) -> MissionSnapshot:
        with self._connect() as db:
            mission = db.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
            if not mission:
                raise KeyError(f"unknown mission: {mission_id}")
            task_rows = db.execute("SELECT * FROM mission_tasks WHERE mission_id=? ORDER BY priority DESC, created_at, task_id", (mission_id,)).fetchall()
            fact_rows = db.execute("SELECT * FROM mission_facts WHERE mission_id=? ORDER BY created_at, fact_id", (mission_id,)).fetchall()
        tasks = [MissionTask(id=row["task_id"], description=row["description"], dependencies=self._loads(row["dependencies"], []), success_criteria=self._loads(row["success_criteria"], []), profile=row["profile"], priority=float(row["priority"]), status=TaskStatus(row["status"]), note=row["note"], output_summary=row["output_summary"], verification=VerificationCertificate.model_validate_json(row["verification_json"]) if row["verification_json"] else None, created_at=row["created_at"], updated_at=row["updated_at"]) for row in task_rows]
        facts = [MissionFact(id=row["fact_id"], claim=row["claim"], evidence_refs=self._loads(row["evidence_refs"], []), source_task_id=row["source_task_id"], status=FactStatus(row["status"]), evidence_strength=float(row["evidence_strength"]), created_at=row["created_at"]) for row in fact_rows]
        return MissionSnapshot(id=mission["id"], external_key=mission["external_key"], goal=mission["goal"], success_criteria=self._loads(mission["success_criteria"], []), status=MissionStatus(mission["status"]), revision=int(mission["revision"]), tasks=tasks, facts=facts, created_at=mission["created_at"], updated_at=mission["updated_at"])

    def list_missions(self, limit: int = 50) -> list[MissionSnapshot]:
        with self._connect() as db:
            rows = db.execute("SELECT id FROM missions ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
        return [self.snapshot(str(row[0])) for row in rows]

    def add_task(self, *, mission_id: str, task_id: str, description: str, dependencies: list[str] | None = None, success_criteria: list[str] | None = None, profile: str | None = None, priority: float = 0.5, expected_revision: int | None = None) -> MissionSnapshot:
        deps = list(dict.fromkeys(dependencies or []))
        if not task_id.strip() or not description.strip():
            raise ValueError("task id and description are required")
        if task_id in deps:
            raise ValueError("task cannot depend on itself")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._check_revision(db, mission_id, expected_revision)
            known = {str(row[0]) for row in db.execute("SELECT task_id FROM mission_tasks WHERE mission_id=?", (mission_id,)).fetchall()}
            unknown = set(deps) - known
            if unknown:
                raise ValueError(f"unknown task dependencies: {sorted(unknown)}")
            db.execute("INSERT INTO mission_tasks(mission_id,task_id,description,dependencies,success_criteria,profile,priority,status,note,output_summary,verification_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?,?)", (mission_id, task_id, description.strip(), json.dumps(deps), json.dumps(success_criteria or [], ensure_ascii=False), profile, max(0.0, min(1.0, float(priority))), TaskStatus.PENDING.value, "", "", now, now))
            self._bump(db, mission_id, current, "task_added", {"task_id": task_id, "dependencies": deps, "profile": profile})
            db.commit()
        return self.snapshot(mission_id)

    def ready_tasks(self, mission_id: str) -> list[MissionTask]:
        snap = self.snapshot(mission_id)
        by_id = {task.id: task for task in snap.tasks}
        ready = [task for task in snap.tasks if task.status == TaskStatus.PENDING and all(by_id[dep].status == TaskStatus.DONE for dep in task.dependencies)]
        return sorted(ready, key=lambda task: (-task.priority, task.id))

    def transition_task(self, *, mission_id: str, task_id: str, target: TaskStatus, note: str = "", expected_revision: int | None = None) -> MissionSnapshot:
        if target == TaskStatus.DONE:
            raise ValueError("DONE requires finalize_task with a verification certificate")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._check_revision(db, mission_id, expected_revision)
            row = db.execute("SELECT dependencies FROM mission_tasks WHERE mission_id=? AND task_id=?", (mission_id, task_id)).fetchone()
            if not row:
                raise KeyError(f"unknown mission task: {task_id}")
            if target == TaskStatus.ACTIVE:
                for dep in self._loads(row["dependencies"], []):
                    dep_row = db.execute("SELECT status FROM mission_tasks WHERE mission_id=? AND task_id=?", (mission_id, dep)).fetchone()
                    if not dep_row or dep_row[0] != TaskStatus.DONE.value:
                        raise ValueError(f"dependency not done: {dep}")
            db.execute("UPDATE mission_tasks SET status=?, note=?, updated_at=? WHERE mission_id=? AND task_id=?", (target.value, note[:2000], now, mission_id, task_id))
            self._bump(db, mission_id, current, "task_transition", {"task_id": task_id, "target": target.value, "note": note[:500]})
            db.commit()
        return self.snapshot(mission_id)

    def record_fact(self, *, mission_id: str, claim: str, certificate: VerificationCertificate, source_task_id: str | None = None, expected_revision: int | None = None, min_evidence_strength: float = 0.25) -> MissionSnapshot:
        if not claim.strip():
            raise ValueError("fact claim cannot be empty")
        if not certificate.evidence_refs:
            raise ValueError("facts require concrete evidence references")
        if certificate.evidence_strength < min_evidence_strength:
            raise ValueError("evidence is too weak to enter canonical mission facts")
        status = FactStatus.REFUTED if certificate.verdict == VerificationVerdict.REFUTED else FactStatus.VERIFIED if certificate.verdict == VerificationVerdict.VERIFIED else FactStatus.SUPPORTED
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._check_revision(db, mission_id, expected_revision)
            if source_task_id and not db.execute("SELECT 1 FROM mission_tasks WHERE mission_id=? AND task_id=?", (mission_id, source_task_id)).fetchone():
                raise KeyError(f"unknown source task: {source_task_id}")
            fact_id = str(uuid4())
            now = _now()
            db.execute("INSERT INTO mission_facts(mission_id,fact_id,claim,evidence_refs,source_task_id,status,evidence_strength,created_at) VALUES(?,?,?,?,?,?,?,?)", (mission_id, fact_id, claim.strip(), json.dumps(certificate.evidence_refs), source_task_id, status.value, float(certificate.evidence_strength), now))
            self._bump(db, mission_id, current, "fact_recorded", {"fact_id": fact_id, "source_task_id": source_task_id, "status": status.value, "evidence_strength": certificate.evidence_strength})
            db.commit()
        return self.snapshot(mission_id)

    def finalize_task(self, *, mission_id: str, task_id: str, certificate: VerificationCertificate, output_summary: str, expected_revision: int | None = None) -> MissionSnapshot:
        if certificate.verdict not in {VerificationVerdict.VERIFIED, VerificationVerdict.SUPPORTED}:
            raise ValueError(f"task completion rejected by certificate verdict: {certificate.verdict.value}")
        if certificate.evidence_strength < self.done_evidence_strength:
            raise ValueError(f"task completion requires evidence strength >= {self.done_evidence_strength:.2f}; got {certificate.evidence_strength:.2f}")
        if not certificate.evidence_refs:
            raise ValueError("task completion requires concrete evidence references")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._check_revision(db, mission_id, expected_revision)
            row = db.execute("SELECT dependencies FROM mission_tasks WHERE mission_id=? AND task_id=?", (mission_id, task_id)).fetchone()
            if not row:
                raise KeyError(f"unknown mission task: {task_id}")
            for dep in self._loads(row["dependencies"], []):
                dep_row = db.execute("SELECT status FROM mission_tasks WHERE mission_id=? AND task_id=?", (mission_id, dep)).fetchone()
                if not dep_row or dep_row[0] != TaskStatus.DONE.value:
                    raise ValueError(f"cannot finalize before dependency is done: {dep}")
            db.execute("UPDATE mission_tasks SET status=?,note='',output_summary=?,verification_json=?,updated_at=? WHERE mission_id=? AND task_id=?", (TaskStatus.DONE.value, output_summary[:12000], certificate.model_dump_json(), now, mission_id, task_id))
            remaining = db.execute("SELECT COUNT(*) FROM mission_tasks WHERE mission_id=? AND status!=?", (mission_id, TaskStatus.DONE.value)).fetchone()[0]
            if int(remaining) == 0:
                db.execute("UPDATE missions SET status=? WHERE id=?", (MissionStatus.DONE.value, mission_id))
            self._bump(db, mission_id, current, "task_finalized", {"task_id": task_id, "verdict": certificate.verdict.value, "evidence_strength": certificate.evidence_strength, "evidence_refs": certificate.evidence_refs})
            db.commit()
        return self.snapshot(mission_id)

    def events(self, mission_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT revision,kind,payload,created_at FROM mission_events WHERE mission_id=? ORDER BY revision", (mission_id,)).fetchall()
        return [{"revision": int(row["revision"]), "kind": row["kind"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]} for row in rows]

    def audit(self, mission_id: str) -> list[StateAuditIssue]:
        snap = self.snapshot(mission_id)
        issues: list[StateAuditIssue] = []
        by_id = {task.id: task for task in snap.tasks}
        for task in snap.tasks:
            for dep in task.dependencies:
                if dep not in by_id:
                    issues.append(StateAuditIssue(severity="error", code="UNKNOWN_DEPENDENCY", task_id=task.id, message=f"task depends on missing task {dep}"))
            if task.status == TaskStatus.DONE:
                cert = task.verification
                if cert is None or cert.evidence_strength < self.done_evidence_strength:
                    issues.append(StateAuditIssue(severity="error", code="DONE_WITHOUT_STRONG_EVIDENCE", task_id=task.id, message="completed task lacks the configured verification strength"))
                for dep in task.dependencies:
                    if dep in by_id and by_id[dep].status != TaskStatus.DONE:
                        issues.append(StateAuditIssue(severity="error", code="DONE_BEFORE_DEPENDENCY", task_id=task.id, message=f"task is done while dependency {dep} is not done"))
        if snap.status == MissionStatus.DONE and any(task.status != TaskStatus.DONE for task in snap.tasks):
            issues.append(StateAuditIssue(severity="error", code="MISSION_DONE_WITH_OPEN_TASKS", message="mission is marked done while tasks remain open"))
        for fact in snap.facts:
            if not fact.evidence_refs:
                issues.append(StateAuditIssue(severity="error", code="FACT_WITHOUT_EVIDENCE", message=f"fact {fact.id} has no evidence references"))
        return issues
