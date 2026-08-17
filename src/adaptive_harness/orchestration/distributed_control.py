from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class LeaseStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    RELEASED = "released"
    EXPIRED = "expired"


class EscrowStatus(str, Enum):
    OPEN = "open"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class LeaseBusy(RuntimeError):
    pass


class LeaseLost(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class TaskLease(BaseModel):
    lease_id: str
    mission_id: str
    task_id: str
    owner: str
    status: LeaseStatus
    acquired_at: datetime
    heartbeat_at: datetime
    expires_at: datetime
    outcome: str = ""


class BudgetEscrow(BaseModel):
    escrow_id: str
    parent_id: str | None = None
    scope: str
    owner: str
    allocated_usd: float = Field(ge=0.0)
    spent_usd: float = Field(ge=0.0)
    reserved_usd: float = Field(ge=0.0)
    available_usd: float = Field(ge=0.0)
    status: EscrowStatus
    created_at: datetime
    updated_at: datetime


class DistributedControlStore:
    """Local durable coordination primitives for bounded hierarchical agent execution.

    Leases prevent duplicate ownership of the same mission task while still allowing recovery after
    a crashed worker. Budget escrows prevent sibling cells from promising the same dollars twice.
    Neither primitive grants capabilities; they only coordinate work already allowed by the parent.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS task_leases (
                    lease_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    status TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_task_lease
                    ON task_leases(mission_id, task_id) WHERE status='active';
                CREATE INDEX IF NOT EXISTS idx_task_lease_lookup
                    ON task_leases(mission_id, task_id, status, expires_at);

                CREATE TABLE IF NOT EXISTS budget_escrows (
                    escrow_id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    scope TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    allocated_usd REAL NOT NULL,
                    spent_usd REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_budget_children
                    ON budget_escrows(parent_id, status);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _lease(row: sqlite3.Row) -> TaskLease:
        return TaskLease(
            lease_id=row["lease_id"],
            mission_id=row["mission_id"],
            task_id=row["task_id"],
            owner=row["owner"],
            status=LeaseStatus(row["status"]),
            acquired_at=_dt(row["acquired_at"]),
            heartbeat_at=_dt(row["heartbeat_at"]),
            expires_at=_dt(row["expires_at"]),
            outcome=row["outcome"],
        )

    def _expire_leases(self, db: sqlite3.Connection, now: datetime) -> None:
        stamp = _iso(now)
        db.execute(
            "UPDATE task_leases SET status='expired' "
            "WHERE status='active' AND expires_at<=?",
            (stamp,),
        )

    def acquire_task(
        self,
        *,
        mission_id: str,
        task_id: str,
        owner: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> TaskLease:
        if not mission_id or not task_id or not owner:
            raise ValueError("mission_id, task_id and owner are required")
        ttl_seconds = max(5, int(ttl_seconds))
        now = now or _utcnow()
        expires = now + timedelta(seconds=ttl_seconds)
        lease_id = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_leases(db, now)
            active = db.execute(
                "SELECT * FROM task_leases "
                "WHERE mission_id=? AND task_id=? AND status='active' LIMIT 1",
                (mission_id, task_id),
            ).fetchone()
            if active:
                current = self._lease(active)
                raise LeaseBusy(
                    f"task {mission_id}/{task_id} is leased by {current.owner} "
                    f"until {current.expires_at.isoformat()}"
                )
            db.execute(
                "INSERT INTO task_leases(lease_id,mission_id,task_id,owner,status,"
                "acquired_at,heartbeat_at,expires_at,outcome) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    lease_id,
                    mission_id,
                    task_id,
                    owner,
                    LeaseStatus.ACTIVE.value,
                    _iso(now),
                    _iso(now),
                    _iso(expires),
                    "",
                ),
            )
            row = db.execute(
                "SELECT * FROM task_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            db.commit()
        return self._lease(row)

    def heartbeat(
        self,
        lease_id: str,
        *,
        owner: str,
        ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> TaskLease:
        ttl_seconds = max(5, int(ttl_seconds))
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_leases(db, now)
            row = db.execute(
                "SELECT * FROM task_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if not row:
                raise LeaseLost(f"unknown lease: {lease_id}")
            lease = self._lease(row)
            if lease.owner != owner or lease.status != LeaseStatus.ACTIVE:
                raise LeaseLost("lease is no longer owned by this worker")
            expires = now + timedelta(seconds=ttl_seconds)
            db.execute(
                "UPDATE task_leases SET heartbeat_at=?, expires_at=? WHERE lease_id=?",
                (_iso(now), _iso(expires), lease_id),
            )
            row = db.execute(
                "SELECT * FROM task_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            db.commit()
        return self._lease(row)

    def finish_lease(
        self,
        lease_id: str,
        *,
        owner: str,
        completed: bool,
        outcome: str = "",
        now: datetime | None = None,
    ) -> TaskLease:
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_leases(db, now)
            row = db.execute(
                "SELECT * FROM task_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            if not row:
                raise LeaseLost(f"unknown lease: {lease_id}")
            lease = self._lease(row)
            if lease.owner != owner or lease.status != LeaseStatus.ACTIVE:
                raise LeaseLost("lease expired, changed owner, or was already finished")
            status = LeaseStatus.COMPLETED if completed else LeaseStatus.RELEASED
            db.execute(
                "UPDATE task_leases SET status=?, heartbeat_at=?, outcome=? WHERE lease_id=?",
                (status.value, _iso(now), outcome[:2000], lease_id),
            )
            row = db.execute(
                "SELECT * FROM task_leases WHERE lease_id=?", (lease_id,)
            ).fetchone()
            db.commit()
        return self._lease(row)

    def current_lease(
        self,
        mission_id: str,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> TaskLease | None:
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_leases(db, now)
            row = db.execute(
                "SELECT * FROM task_leases WHERE mission_id=? AND task_id=? "
                "AND status='active' ORDER BY acquired_at DESC LIMIT 1",
                (mission_id, task_id),
            ).fetchone()
            db.commit()
        return self._lease(row) if row else None

    # ----------------------------- budget escrow -----------------------------

    def _reserved(self, db: sqlite3.Connection, escrow_id: str) -> float:
        row = db.execute(
            "SELECT COALESCE(SUM(allocated_usd),0) FROM budget_escrows "
            "WHERE parent_id=? AND status='open'",
            (escrow_id,),
        ).fetchone()
        return float(row[0] or 0.0)

    def _escrow_from_row(self, db: sqlite3.Connection, row: sqlite3.Row) -> BudgetEscrow:
        reserved = self._reserved(db, row["escrow_id"]) if row["status"] == "open" else 0.0
        allocated = float(row["allocated_usd"])
        spent = float(row["spent_usd"])
        available = max(0.0, allocated - spent - reserved) if row["status"] == "open" else 0.0
        return BudgetEscrow(
            escrow_id=row["escrow_id"],
            parent_id=row["parent_id"],
            scope=row["scope"],
            owner=row["owner"],
            allocated_usd=allocated,
            spent_usd=spent,
            reserved_usd=reserved,
            available_usd=available,
            status=EscrowStatus(row["status"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    def escrow(self, escrow_id: str) -> BudgetEscrow:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"unknown escrow: {escrow_id}")
            return self._escrow_from_row(db, row)

    def create_root_escrow(
        self,
        *,
        scope: str,
        owner: str,
        allocated_usd: float,
        now: datetime | None = None,
    ) -> BudgetEscrow:
        amount = float(allocated_usd)
        if amount < 0:
            raise ValueError("allocated_usd cannot be negative")
        now = now or _utcnow()
        escrow_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO budget_escrows(escrow_id,parent_id,scope,owner,allocated_usd,"
                "spent_usd,status,created_at,updated_at) VALUES(?,?,?,?,?,0,'open',?,?)",
                (escrow_id, None, scope, owner, amount, _iso(now), _iso(now)),
            )
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            db.commit()
            return self._escrow_from_row(db, row)

    def allocate_child_escrow(
        self,
        parent_id: str,
        *,
        scope: str,
        owner: str,
        allocated_usd: float,
        now: datetime | None = None,
    ) -> BudgetEscrow:
        amount = float(allocated_usd)
        if amount < 0:
            raise ValueError("allocated_usd cannot be negative")
        now = now or _utcnow()
        child_id = str(uuid4())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            parent_row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (parent_id,)
            ).fetchone()
            if not parent_row:
                raise KeyError(f"unknown parent escrow: {parent_id}")
            parent = self._escrow_from_row(db, parent_row)
            if parent.status != EscrowStatus.OPEN:
                raise BudgetExceeded("parent escrow is not open")
            if amount > parent.available_usd + 1e-12:
                raise BudgetExceeded(
                    f"child allocation ${amount:.6f} exceeds available ${parent.available_usd:.6f}"
                )
            db.execute(
                "INSERT INTO budget_escrows(escrow_id,parent_id,scope,owner,allocated_usd,"
                "spent_usd,status,created_at,updated_at) VALUES(?,?,?,?,?,0,'open',?,?)",
                (child_id, parent_id, scope, owner, amount, _iso(now), _iso(now)),
            )
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (child_id,)
            ).fetchone()
            db.commit()
            return self._escrow_from_row(db, row)

    def charge(
        self,
        escrow_id: str,
        amount_usd: float,
        *,
        now: datetime | None = None,
    ) -> BudgetEscrow:
        amount = float(amount_usd)
        if amount < 0:
            raise ValueError("amount_usd cannot be negative")
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"unknown escrow: {escrow_id}")
            current = self._escrow_from_row(db, row)
            if current.status != EscrowStatus.OPEN:
                raise BudgetExceeded("escrow is not open")
            if amount > current.available_usd + 1e-12:
                raise BudgetExceeded(
                    f"charge ${amount:.6f} exceeds available ${current.available_usd:.6f}"
                )
            db.execute(
                "UPDATE budget_escrows SET spent_usd=spent_usd+?, updated_at=? WHERE escrow_id=?",
                (amount, _iso(now), escrow_id),
            )
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            db.commit()
            return self._escrow_from_row(db, row)

    def settle_escrow(
        self,
        escrow_id: str,
        *,
        now: datetime | None = None,
    ) -> BudgetEscrow:
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"unknown escrow: {escrow_id}")
            current = self._escrow_from_row(db, row)
            if current.status == EscrowStatus.SETTLED:
                db.commit()
                return current
            if current.status != EscrowStatus.OPEN:
                raise BudgetExceeded("only open escrows can be settled")
            open_children = db.execute(
                "SELECT COUNT(*) FROM budget_escrows WHERE parent_id=? AND status='open'",
                (escrow_id,),
            ).fetchone()[0]
            if open_children:
                raise BudgetExceeded("cannot settle escrow while child reservations remain open")
            if current.parent_id:
                parent_row = db.execute(
                    "SELECT * FROM budget_escrows WHERE escrow_id=?", (current.parent_id,)
                ).fetchone()
                if not parent_row:
                    raise KeyError(f"unknown parent escrow: {current.parent_id}")
                parent = self._escrow_from_row(db, parent_row)
                if parent.status != EscrowStatus.OPEN:
                    raise BudgetExceeded("parent escrow closed before child settlement")
                # The child's full allocation was reserved, so charging only actual child spend
                # cannot exceed the parent envelope. The unused reservation is released on settle.
                db.execute(
                    "UPDATE budget_escrows SET spent_usd=spent_usd+?, updated_at=? "
                    "WHERE escrow_id=?",
                    (current.spent_usd, _iso(now), current.parent_id),
                )
            db.execute(
                "UPDATE budget_escrows SET status='settled', updated_at=? WHERE escrow_id=?",
                (_iso(now), escrow_id),
            )
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            db.commit()
            return self._escrow_from_row(db, row)

    def cancel_escrow(
        self,
        escrow_id: str,
        *,
        now: datetime | None = None,
    ) -> BudgetEscrow:
        now = now or _utcnow()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"unknown escrow: {escrow_id}")
            current = self._escrow_from_row(db, row)
            if current.status != EscrowStatus.OPEN:
                raise BudgetExceeded("only open escrows can be cancelled")
            if current.spent_usd > 1e-12:
                raise BudgetExceeded("cannot cancel an escrow after spend was recorded")
            if current.reserved_usd > 1e-12:
                raise BudgetExceeded("cannot cancel an escrow with open child reservations")
            db.execute(
                "UPDATE budget_escrows SET status='cancelled', updated_at=? WHERE escrow_id=?",
                (_iso(now), escrow_id),
            )
            row = db.execute(
                "SELECT * FROM budget_escrows WHERE escrow_id=?", (escrow_id,)
            ).fetchone()
            db.commit()
            return self._escrow_from_row(db, row)
