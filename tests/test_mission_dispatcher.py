from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaptive_harness.contracts import Observation, TraceEvent, TrustLevel
from adaptive_harness.orchestration.cell_runtime import (
    CellLimits,
    CellPlan,
    HierarchicalCellRuntime,
    LeafOutcome,
)
from adaptive_harness.orchestration.distributed_control import (
    DistributedControlStore,
    LeaseStatus,
)
from adaptive_harness.orchestration.mission_dispatcher import (
    DispatchStatus,
    MissionDispatcher,
)
from adaptive_harness.orchestration.state_plane import TaskStateStore, TaskStatus
from adaptive_harness.runtime.trace_store import TraceStore


def _stack(tmp_path: Path, *, leaf_attempts: int = 2, ttl: int = 30):
    state = TaskStateStore(tmp_path / "state.sqlite3")
    control = DistributedControlStore(tmp_path / "control.sqlite3")
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    cells = HierarchicalCellRuntime(
        control,
        limits=CellLimits(
            max_depth=1,
            max_cells=4,
            max_leaf_attempts=leaf_attempts,
            min_child_budget_usd=0.001,
        ),
    )
    dispatcher = MissionDispatcher(
        state=state,
        control=control,
        traces=traces,
        cells=cells,
        lease_ttl_seconds=ttl,
        context_budget_tokens=500,
    )
    return state, control, traces, dispatcher


def _mission_with_task(state: TaskStateStore, *, description: str = "Run pytest tests for code"):
    mission = state.create_mission(goal="Ship a verified change")
    mission = state.add_task(
        mission_id=mission.id,
        task_id="t1",
        description=description,
        success_criteria=["pytest tests pass"],
        profile="code",
        priority=0.9,
        expected_revision=mission.revision,
    )
    return mission


def _append_passing_test_observation(traces: TraceStore, call_id: str) -> None:
    obs = Observation(
        call_id=call_id,
        tool_name="verify_workspace_command",
        ok=True,
        content="all tests passed",
        trust=TrustLevel.TOOL,
        metadata={
            "verification_signal": "deterministic_pass",
            "verification_kind": "tests",
            "verification_claim": "pytest tests pass",
        },
    )
    traces.append(
        TraceEvent(
            run_id="leaf-run",
            kind="tool_observation",
            payload=obs.model_dump(mode="json"),
        )
    )


@pytest.mark.asyncio
async def test_verified_complete_cell_finalizes_task_and_lease(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path, leaf_attempts=1)
    mission = _mission_with_task(state)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def leaf(spec, allowance):
        _append_passing_test_observation(traces, "check-1")
        return LeafOutcome(
            answer="tests pass",
            success=True,
            cost_usd=0.0,
            confidence=0.95,
            evidence_refs=["check-1"],
        )

    result = await dispatcher.dispatch_one(
        mission.id,
        owner="dispatcher-a",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.status == DispatchStatus.COMPLETED
    assert result.verification is not None
    assert result.verification.evidence_strength >= 0.60
    snap = state.snapshot(mission.id)
    assert next(task for task in snap.tasks if task.id == "t1").status == TaskStatus.DONE
    latest = dispatcher._latest_lease(mission.id, "t1")
    assert latest is not None and latest.status == LeaseStatus.COMPLETED


@pytest.mark.asyncio
async def test_model_only_complete_cell_is_blocked_not_done(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path, leaf_attempts=1)
    mission = _mission_with_task(state)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def leaf(spec, allowance):
        return LeafOutcome(answer="I think it is done", success=True, confidence=0.99)

    result = await dispatcher.dispatch_one(
        mission.id,
        owner="dispatcher-a",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.status == DispatchStatus.BLOCKED
    assert result.verification is not None
    assert result.verification.evidence_strength < 0.60
    task = state.snapshot(mission.id).tasks[0]
    assert task.status == TaskStatus.BLOCKED
    latest = dispatcher._latest_lease(mission.id, "t1")
    assert latest is not None and latest.status == LeaseStatus.RELEASED


def test_expired_dispatcher_lease_requeues_active_task(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path, ttl=5)
    mission = _mission_with_task(state)
    lease = control.acquire_task(
        mission_id=mission.id,
        task_id="t1",
        owner="crashed-worker",
        ttl_seconds=5,
        now=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    state.transition_task(
        mission_id=mission.id,
        task_id="t1",
        target=TaskStatus.ACTIVE,
        expected_revision=state.snapshot(mission.id).revision,
    )

    recovered = dispatcher.recover_expired_tasks(mission.id)
    assert recovered == ["t1"]
    assert state.snapshot(mission.id).tasks[0].status == TaskStatus.PENDING
    latest = dispatcher._latest_lease(mission.id, "t1")
    assert latest is not None
    assert latest.lease_id == lease.lease_id
    assert latest.status == LeaseStatus.EXPIRED


def test_active_task_without_dispatcher_lease_is_not_auto_requeued(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path)
    mission = _mission_with_task(state)
    state.transition_task(
        mission_id=mission.id,
        task_id="t1",
        target=TaskStatus.ACTIVE,
        expected_revision=state.snapshot(mission.id).revision,
    )
    assert dispatcher.recover_expired_tasks(mission.id) == []
    assert state.snapshot(mission.id).tasks[0].status == TaskStatus.ACTIVE


@pytest.mark.asyncio
async def test_unrelated_revision_advance_is_safely_rebased_for_finalization(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path, leaf_attempts=1)
    mission = _mission_with_task(state)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def leaf(spec, allowance):
        # Simulate an independent manager adding future work while t1 is executing. The mission
        # revision changes, but the t1 task contract remains identical and ACTIVE.
        snap = state.snapshot(mission.id)
        state.add_task(
            mission_id=mission.id,
            task_id="later",
            description="Independent follow-up",
            priority=0.2,
            expected_revision=snap.revision,
        )
        _append_passing_test_observation(traces, "check-rebase")
        return LeafOutcome(
            answer="verified after concurrent mission update",
            success=True,
            evidence_refs=["check-rebase"],
        )

    result = await dispatcher.dispatch_one(
        mission.id,
        owner="dispatcher-a",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.status == DispatchStatus.COMPLETED
    snap = state.snapshot(mission.id)
    assert next(task for task in snap.tasks if task.id == "t1").status == TaskStatus.DONE
    assert next(task for task in snap.tasks if task.id == "later").status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_resource_blocked_cell_never_finalizes_task(tmp_path: Path):
    state, control, traces, dispatcher = _stack(tmp_path, leaf_attempts=1)
    mission = _mission_with_task(state, description="Analyze two independent code risks")

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(
            children=[
                spec.model_copy(update={"id": "a", "task": "risk A", "decomposable": False}),
                spec.model_copy(update={"id": "b", "task": "risk B", "decomposable": False}),
            ]
        )

    async def leaf(spec, allowance):
        return LeafOutcome(answer=spec.id, success=True)

    result = await dispatcher.dispatch_one(
        mission.id,
        owner="dispatcher-a",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.status == DispatchStatus.BLOCKED
    assert result.cell_status is not None
    assert state.snapshot(mission.id).tasks[0].status == TaskStatus.BLOCKED
