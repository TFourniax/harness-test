from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from adaptive_harness.contracts import RunStatus
from adaptive_harness.orchestration.cell_runtime import (
    CellExecutionResult,
    CellPlan,
    CellSpec,
    CellStatus,
    HierarchicalCellRuntime,
    LeafOutcome,
    LeafRunner,
    Planner,
)
from adaptive_harness.orchestration.context_budget import MissionContextBuilder
from adaptive_harness.orchestration.contracts import (
    VerificationCertificate,
    VerificationVerdict,
    WorkItem,
)
from adaptive_harness.orchestration.distributed_control import (
    DistributedControlStore,
    LeaseBusy,
    LeaseLost,
    LeaseStatus,
    TaskLease,
)
from adaptive_harness.orchestration.state_plane import (
    MissionTask,
    RevisionConflict,
    TaskStateStore,
    TaskStatus,
)
from adaptive_harness.orchestration.verification import VerificationEngine
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.trace_store import TraceStore


class DispatchStatus(str, Enum):
    IDLE = "idle"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CONFLICT = "conflict"
    FAILED = "failed"


class MissionDispatchResult(BaseModel):
    mission_id: str
    status: DispatchStatus
    task_id: str | None = None
    lease_id: str | None = None
    cell_status: CellStatus | None = None
    answer: str = ""
    cost_usd: float = Field(default=0.0, ge=0.0)
    context_tokens: int = 0
    evidence_refs: list[str] = Field(default_factory=list)
    verification: VerificationCertificate | None = None
    note: str = ""
    recovered_tasks: list[str] = Field(default_factory=list)


class MissionDispatcher:
    """Lease-safe bridge between durable mission state and hierarchical reasoning cells.

    The dispatcher is deliberately conservative:
    - only PENDING dependency-ready tasks are eligible;
    - task ownership is established before ACTIVE state;
    - a background heartbeat prevents duplicate recovery while a long cell is running;
    - a lost lease makes the computed result non-committable;
    - only COMPLETE cell results can attempt finalization;
    - evidence references must resolve to real, task-fresh trace observations;
    - mission revision conflicts are rebased only when the task contract itself is unchanged.
    """

    def __init__(
        self,
        *,
        state: TaskStateStore,
        control: DistributedControlStore,
        traces: TraceStore,
        cells: HierarchicalCellRuntime,
        context_builder: MissionContextBuilder | None = None,
        verifier: VerificationEngine | None = None,
        lease_ttl_seconds: int = 300,
        context_budget_tokens: int = 3000,
    ) -> None:
        self.state = state
        self.control = control
        self.traces = traces
        self.cells = cells
        self.context_builder = context_builder or MissionContextBuilder()
        self.verifier = verifier or VerificationEngine()
        self.lease_ttl_seconds = max(5, int(lease_ttl_seconds))
        self.context_budget_tokens = max(64, int(context_budget_tokens))

    @staticmethod
    def _task_contract(task: MissionTask) -> tuple[Any, ...]:
        return (
            task.id,
            task.description,
            tuple(task.dependencies),
            tuple(task.success_criteria),
            task.profile,
            task.created_at,
        )

    def _latest_lease(self, mission_id: str, task_id: str) -> TaskLease | None:
        """Read the most recent lease after forcing TTL expiry evaluation.

        The query is intentionally read-only and local to the coordination schema. It is used only
        for crash recovery: an ACTIVE task is auto-requeued iff it has concrete lease history whose
        latest state became EXPIRED. ACTIVE tasks with no dispatcher lease history are left alone.
        """
        self.control.current_lease(mission_id, task_id)
        with sqlite3.connect(self.control.path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM task_leases WHERE mission_id=? AND task_id=? "
                "ORDER BY acquired_at DESC, lease_id DESC LIMIT 1",
                (mission_id, task_id),
            ).fetchone()
        return self.control._lease(row) if row else None  # coordination-internal read model

    def recover_expired_tasks(self, mission_id: str) -> list[str]:
        recovered: list[str] = []
        # Multiple tasks may be recovered; each mutation advances the global mission revision.
        for _ in range(256):
            snap = self.state.snapshot(mission_id)
            candidate = None
            for task in snap.tasks:
                if task.status != TaskStatus.ACTIVE:
                    continue
                latest = self._latest_lease(mission_id, task.id)
                if latest and latest.status == LeaseStatus.EXPIRED:
                    candidate = task
                    break
            if candidate is None:
                break
            try:
                self.state.transition_task(
                    mission_id=mission_id,
                    task_id=candidate.id,
                    target=TaskStatus.PENDING,
                    note=(
                        f"Automatically requeued after dispatcher lease {latest.lease_id} expired; "
                        "previous completion state is unknown and must be re-verified."
                    ),
                    expected_revision=snap.revision,
                )
                recovered.append(candidate.id)
            except RevisionConflict:
                continue
        return recovered

    def _claim_ready_task(self, mission_id: str, owner: str) -> tuple[TaskLease, MissionTask] | None:
        # A lease is acquired first, then readiness is revalidated from canonical state before ACTIVE.
        for task in self.state.ready_tasks(mission_id):
            try:
                lease = self.control.acquire_task(
                    mission_id=mission_id,
                    task_id=task.id,
                    owner=owner,
                    ttl_seconds=self.lease_ttl_seconds,
                )
            except LeaseBusy:
                continue
            snap = self.state.snapshot(mission_id)
            current = next((item for item in snap.tasks if item.id == task.id), None)
            done_ids = {item.id for item in snap.tasks if item.status == TaskStatus.DONE}
            still_ready = (
                current is not None
                and current.status == TaskStatus.PENDING
                and all(dep in done_ids for dep in current.dependencies)
                and self._task_contract(current) == self._task_contract(task)
            )
            if not still_ready:
                self.control.finish_lease(
                    lease.lease_id,
                    owner=owner,
                    completed=False,
                    outcome="canonical readiness changed after lease acquisition",
                )
                continue
            try:
                active = self.state.transition_task(
                    mission_id=mission_id,
                    task_id=task.id,
                    target=TaskStatus.ACTIVE,
                    note=f"Leased to dispatcher owner {owner} as {lease.lease_id}",
                    expected_revision=snap.revision,
                )
            except RevisionConflict:
                self.control.finish_lease(
                    lease.lease_id,
                    owner=owner,
                    completed=False,
                    outcome="revision conflict while activating task",
                )
                continue
            active_task = next(item for item in active.tasks if item.id == task.id)
            return lease, active_task
        return None

    async def _heartbeat_loop(
        self,
        lease_id: str,
        *,
        owner: str,
        stop: asyncio.Event,
        lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, self.lease_ttl_seconds / 3.0)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            try:
                self.control.heartbeat(
                    lease_id,
                    owner=owner,
                    ttl_seconds=self.lease_ttl_seconds,
                )
            except LeaseLost:
                lost.set()
                return

    @staticmethod
    def _fresh_observations(task: MissionTask, refs: list[str], traces: TraceStore):
        unique = list(dict.fromkeys(refs))
        observations = traces.find_observations(unique)
        found = {obs.call_id for obs in observations}
        missing = [ref for ref in unique if ref not in found]
        boundary = datetime.fromisoformat(task.created_at)
        if boundary.tzinfo is None:
            boundary = boundary.replace(tzinfo=timezone.utc)
        stale = [
            obs.call_id
            for obs in observations
            if obs.created_at.astimezone(timezone.utc) < boundary.astimezone(timezone.utc)
        ]
        return observations, missing, stale

    def _certificate(
        self, task: MissionTask, cell: CellExecutionResult
    ) -> VerificationCertificate:
        observations, missing, stale = self._fresh_observations(
            task, cell.evidence_refs, self.traces
        )
        if missing or stale:
            reasons = []
            if missing:
                reasons.append(f"unresolved evidence references: {missing}")
            if stale:
                reasons.append(f"evidence predates task creation: {stale}")
            return VerificationCertificate(
                verdict=VerificationVerdict.UNVERIFIED,
                evidence_strength=0.0,
                score=0.0,
                scope_coverage=0.0,
                evidence_refs=[obs.call_id for obs in observations],
                reasons=reasons,
            )
        synthetic = RunResult(
            run_id=f"mission:{task.id}",
            status=RunStatus.SUCCEEDED if cell.status == CellStatus.COMPLETE else RunStatus.FAILED,
            answer=cell.answer,
            observations=observations,
            reported_cost_usd=cell.cost_usd,
        )
        work = WorkItem(
            id=task.id,
            task=(
                task.description
                + ("\nSUCCESS CRITERIA:\n- " + "\n- ".join(task.success_criteria)
                   if task.success_criteria else "")
            ),
            dependencies=task.dependencies,
            profile=task.profile,
            critical=task.priority >= 0.85,
        )
        return self.verifier.certify(work, [synthetic])

    def _current_unchanged_task(self, mission_id: str, original: MissionTask):
        snap = self.state.snapshot(mission_id)
        current = next((item for item in snap.tasks if item.id == original.id), None)
        if (
            current is None
            or current.status != TaskStatus.ACTIVE
            or self._task_contract(current) != self._task_contract(original)
        ):
            raise RevisionConflict(
                "task contract/status changed during execution; computed result requires reconciliation"
            )
        return snap, current

    def _block_if_unchanged(self, mission_id: str, task: MissionTask, note: str) -> None:
        for _ in range(3):
            snap, _current = self._current_unchanged_task(mission_id, task)
            try:
                self.state.transition_task(
                    mission_id=mission_id,
                    task_id=task.id,
                    target=TaskStatus.BLOCKED,
                    note=note[:2000],
                    expected_revision=snap.revision,
                )
                return
            except RevisionConflict:
                continue
        raise RevisionConflict("could not safely record blocked task after concurrent mission updates")

    def _finalize_if_unchanged(
        self,
        mission_id: str,
        task: MissionTask,
        certificate: VerificationCertificate,
        output_summary: str,
    ) -> None:
        # Global mission revision may advance because an independent sibling finished. Rebase only
        # when this exact task contract is still ACTIVE and unchanged; otherwise fail closed.
        for _ in range(3):
            snap, _current = self._current_unchanged_task(mission_id, task)
            try:
                self.state.finalize_task(
                    mission_id=mission_id,
                    task_id=task.id,
                    certificate=certificate,
                    output_summary=output_summary[:8000],
                    expected_revision=snap.revision,
                )
                return
            except RevisionConflict:
                continue
        raise RevisionConflict("could not safely finalize task after concurrent mission updates")

    async def dispatch_one(
        self,
        mission_id: str,
        *,
        owner: str,
        budget_usd: float,
        planner: Planner,
        leaf_runner: LeafRunner,
        context_budget_tokens: int | None = None,
    ) -> MissionDispatchResult:
        recovered = self.recover_expired_tasks(mission_id)
        claimed = self._claim_ready_task(mission_id, owner)
        if claimed is None:
            return MissionDispatchResult(
                mission_id=mission_id,
                status=DispatchStatus.IDLE,
                note="no dependency-ready unleased task",
                recovered_tasks=recovered,
            )
        lease, task = claimed
        active_snapshot = self.state.snapshot(mission_id)
        context = self.context_builder.build(
            active_snapshot,
            focus=task.description,
            focus_task_id=task.id,
            max_tokens=context_budget_tokens or self.context_budget_tokens,
        )
        root = CellSpec(
            id=f"mission-{task.id}",
            task=task.description,
            success_criteria=task.success_criteria,
            constraints=[
                "Canonical mission state is external to this prompt. Do not invent state transitions.",
                "Return concrete evidence references from executed tools whenever possible.",
                "MISSION STATE SLICE:\n" + context.text,
            ],
            profile=task.profile,
            difficulty=max(0.2, min(1.0, 0.35 + 0.55 * task.priority)),
            critical=task.priority >= 0.85,
            decomposable=True,
        )

        stop = asyncio.Event()
        lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(lease.lease_id, owner=owner, stop=stop, lost=lost)
        )
        try:
            cell = await self.cells.execute(
                root,
                owner=owner,
                budget_usd=max(0.0, float(budget_usd)),
                planner=planner,
                leaf_runner=leaf_runner,
            )
        except Exception as exc:
            stop.set()
            await heartbeat_task
            if not lost.is_set():
                try:
                    self._block_if_unchanged(
                        mission_id,
                        task,
                        f"Dispatcher execution failed: {type(exc).__name__}: {exc}",
                    )
                    self.control.finish_lease(
                        lease.lease_id,
                        owner=owner,
                        completed=False,
                        outcome=f"execution failed: {type(exc).__name__}: {exc}",
                    )
                except (RevisionConflict, LeaseLost):
                    pass
            return MissionDispatchResult(
                mission_id=mission_id,
                task_id=task.id,
                lease_id=lease.lease_id,
                status=DispatchStatus.FAILED,
                note=f"{type(exc).__name__}: {exc}",
                context_tokens=context.used_tokens,
                recovered_tasks=recovered,
            )
        finally:
            if not stop.is_set():
                stop.set()
                await heartbeat_task

        if lost.is_set():
            return MissionDispatchResult(
                mission_id=mission_id,
                task_id=task.id,
                lease_id=lease.lease_id,
                status=DispatchStatus.CONFLICT,
                cell_status=cell.status,
                answer=cell.answer,
                cost_usd=cell.cost_usd,
                context_tokens=context.used_tokens,
                evidence_refs=cell.evidence_refs,
                note="lease ownership was lost during execution; result was not committed",
                recovered_tasks=recovered,
            )

        certificate = self._certificate(task, cell)
        try:
            if cell.status == CellStatus.COMPLETE:
                try:
                    self._finalize_if_unchanged(
                        mission_id, task, certificate, cell.answer
                    )
                except ValueError as exc:
                    self._block_if_unchanged(
                        mission_id,
                        task,
                        "Cell completed but evidence gate rejected durable completion: " + str(exc),
                    )
                    self.control.finish_lease(
                        lease.lease_id,
                        owner=owner,
                        completed=False,
                        outcome="complete computation, insufficient durable evidence",
                    )
                    return MissionDispatchResult(
                        mission_id=mission_id,
                        task_id=task.id,
                        lease_id=lease.lease_id,
                        status=DispatchStatus.BLOCKED,
                        cell_status=cell.status,
                        answer=cell.answer,
                        cost_usd=cell.cost_usd,
                        context_tokens=context.used_tokens,
                        evidence_refs=cell.evidence_refs,
                        verification=certificate,
                        note=str(exc),
                        recovered_tasks=recovered,
                    )
                self.control.finish_lease(
                    lease.lease_id,
                    owner=owner,
                    completed=True,
                    outcome="task finalized with evidence certificate",
                )
                return MissionDispatchResult(
                    mission_id=mission_id,
                    task_id=task.id,
                    lease_id=lease.lease_id,
                    status=DispatchStatus.COMPLETED,
                    cell_status=cell.status,
                    answer=cell.answer,
                    cost_usd=cell.cost_usd,
                    context_tokens=context.used_tokens,
                    evidence_refs=cell.evidence_refs,
                    verification=certificate,
                    recovered_tasks=recovered,
                )

            self._block_if_unchanged(
                mission_id,
                task,
                f"Hierarchical cell ended {cell.status.value}: {cell.reason}; "
                + ("; ".join(cell.blocked_reasons) if cell.blocked_reasons else cell.answer[:1200]),
            )
            self.control.finish_lease(
                lease.lease_id,
                owner=owner,
                completed=False,
                outcome=f"cell status {cell.status.value}",
            )
            return MissionDispatchResult(
                mission_id=mission_id,
                task_id=task.id,
                lease_id=lease.lease_id,
                status=DispatchStatus.BLOCKED,
                cell_status=cell.status,
                answer=cell.answer,
                cost_usd=cell.cost_usd,
                context_tokens=context.used_tokens,
                evidence_refs=cell.evidence_refs,
                verification=certificate,
                note=f"cell did not complete: {cell.status.value}",
                recovered_tasks=recovered,
            )
        except RevisionConflict as exc:
            try:
                self.control.finish_lease(
                    lease.lease_id,
                    owner=owner,
                    completed=False,
                    outcome=f"state reconciliation required: {exc}",
                )
            except LeaseLost:
                pass
            return MissionDispatchResult(
                mission_id=mission_id,
                task_id=task.id,
                lease_id=lease.lease_id,
                status=DispatchStatus.CONFLICT,
                cell_status=cell.status,
                answer=cell.answer,
                cost_usd=cell.cost_usd,
                context_tokens=context.used_tokens,
                evidence_refs=cell.evidence_refs,
                verification=certificate,
                note=str(exc),
                recovered_tasks=recovered,
            )
