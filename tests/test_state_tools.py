from pathlib import Path

import pytest

from adaptive_harness.contracts import Observation, ToolCall, TraceEvent, TrustLevel
from adaptive_harness.orchestration.state_plane import MissionStatus, TaskStateStore, TaskStatus
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore
from adaptive_harness.tools.state_tools import register_state_tools


def _observation(call_id: str, *, returncode: int = 0) -> Observation:
    return Observation(
        call_id=call_id,
        tool_name="verify_workspace_command",
        ok=True,
        content='{"returncode": 0}',
        metadata={
            "verification_signal": (
                "deterministic_pass" if returncode == 0 else "deterministic_fail"
            ),
            "verification_kind": "tests",
            "verification_claim": "pytest parser tests pass",
            "returncode": returncode,
            "deterministic": True,
        },
        trust=TrustLevel.TOOL,
    )


def _append_obs(traces: TraceStore, obs: Observation) -> None:
    traces.append(
        TraceEvent(
            run_id="run-1",
            kind="tool_observation",
            payload=obs.model_dump(mode="json"),
        )
    )


@pytest.mark.asyncio
async def test_model_cannot_finalize_with_invented_evidence_id(tmp_path: Path):
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    store = TaskStateStore(tmp_path / "state.sqlite3")
    mission = store.create_mission(goal="repair parser")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="repair",
        description="fix parser tests",
        profile="code",
        expected_revision=mission.revision,
    )
    registry = ToolRegistry()
    register_state_tools(registry, store=store, traces=traces)
    obs = await registry.execute(
        ToolCall(
            name="mission_state_finalize",
            arguments={
                "mission_id": mission.id,
                "task_id": "repair",
                "evidence_call_ids": ["invented-proof"],
                "expected_revision": mission.revision,
            },
        )
    )
    assert not obs.ok
    assert "unknown/non-executed" in obs.content
    assert store.snapshot(mission.id).tasks[0].status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_executed_task_bound_verification_can_finalize(tmp_path: Path):
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    store = TaskStateStore(tmp_path / "state.sqlite3", done_evidence_strength=0.6)
    mission = store.create_mission(goal="repair parser")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="repair",
        description="fix parser tests",
        profile="code",
        expected_revision=mission.revision,
    )
    _append_obs(traces, _observation("real-test"))
    registry = ToolRegistry()
    register_state_tools(registry, store=store, traces=traces)
    result = await registry.execute(
        ToolCall(
            name="mission_state_finalize",
            arguments={
                "mission_id": mission.id,
                "task_id": "repair",
                "evidence_call_ids": ["real-test"],
                "output_summary": "parser regression fixed",
                "expected_revision": mission.revision,
            },
        )
    )
    assert result.ok
    snap = store.snapshot(mission.id)
    assert snap.status == MissionStatus.DONE
    assert snap.tasks[0].status == TaskStatus.DONE
    assert snap.tasks[0].verification is not None
    assert "real-test" in snap.tasks[0].verification.evidence_refs


@pytest.mark.asyncio
async def test_direct_done_transition_is_not_a_tool_option(tmp_path: Path):
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    store = TaskStateStore(tmp_path / "state.sqlite3")
    mission = store.create_mission(goal="mission")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="task",
        expected_revision=mission.revision,
    )
    registry = ToolRegistry()
    register_state_tools(registry, store=store, traces=traces)
    result = await registry.execute(
        ToolCall(
            name="mission_state_transition",
            arguments={
                "mission_id": mission.id,
                "task_id": "a",
                "target": "done",
                "expected_revision": mission.revision,
            },
        )
    )
    assert not result.ok
    assert store.snapshot(mission.id).tasks[0].status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_child_registry_can_read_but_has_no_state_write_tools(tmp_path: Path):
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    store = TaskStateStore(tmp_path / "state.sqlite3")
    mission = store.create_mission(goal="mission")
    registry = ToolRegistry()
    register_state_tools(registry, store=store, traces=traces, writable=False)
    names = {spec.name for spec in registry.specs()}
    assert names == {"mission_state_read", "mission_state_audit"}
    result = await registry.execute(
        ToolCall(
            name="mission_state_read",
            arguments={"mission_id": mission.id, "budget_tokens": 256},
        )
    )
    assert result.ok
    assert mission.id in result.content


def test_trace_lookup_returns_only_executed_observations(tmp_path: Path):
    traces = TraceStore(str(tmp_path / "traces.sqlite3"))
    _append_obs(traces, _observation("evidence-1"))
    found = traces.find_observations(["missing", "evidence-1"])
    assert [obs.call_id for obs in found] == ["evidence-1"]
