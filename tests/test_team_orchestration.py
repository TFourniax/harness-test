import json
from pathlib import Path

import pytest

from adaptive_harness.config import HarnessConfig, ModelRole, TeamConfig
from adaptive_harness.contracts import Goal, ModelTurn, RunStatus, ToolCall, ToolExecutionResult, ToolSpec
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.orchestration.team import TeamOrchestrator
from adaptive_harness.providers.fake import FakeProvider
from adaptive_harness.runtime.agent import AgentRuntime, RunResult
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore


@pytest.mark.asyncio
async def test_team_builds_sparse_plan_routes_cheap_workers_and_synthesizes(tmp_path: Path):
    plan = {
        "rationale": "parallel independent checks",
        "tasks": [
            {
                "id": "a",
                "task": "inspect source A",
                "dependencies": [],
                "profile": "research",
                "difficulty": 0.3,
                "expected_value": 0.9,
                "critical": False,
                "freshness": "volatile",
                "cacheable": False,
            },
            {
                "id": "b",
                "task": "inspect source B",
                "dependencies": [],
                "profile": "research",
                "difficulty": 0.4,
                "expected_value": 0.8,
                "critical": False,
                "freshness": "volatile",
                "cacheable": False,
            },
        ],
    }
    synthesis = {
        "answer": "combined answer",
        "confidence": 0.93,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider(
        [
            ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.001}),
            ModelTurn(content=json.dumps(synthesis), usage={"cost_usd": 0.002}),
        ]
    )
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        max_parallel_agents=4,
        team=TeamConfig(enabled=True, max_agents=4, max_rounds=2),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        return RunResult(
            run_id=f"child-{len(seen)}",
            status=RunStatus.SUCCEEDED,
            answer=f"worker result {len(seen)}",
            reported_cost_usd=0.004,
        )

    orchestrator = TeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="complex research",
        success_criteria=[],
        constraints=[],
        max_agents=4,
        max_cost_usd=None,
    )
    assert result.content == "combined answer"
    assert len(seen) == 2
    assert all(goal.model_role == "cheap" for goal in seen)
    assert result.metadata["team_agents_executed"] == 2
    assert result.metadata["model_cost_usd"] == pytest.approx(0.011)


@pytest.mark.asyncio
async def test_internal_team_model_cost_counts_against_parent_budget(tmp_path: Path):
    provider = FakeProvider(
        [
            ModelTurn(tool_calls=[ToolCall(name="costly_team", arguments={})]),
            ModelTurn(content="should not be reached"),
        ]
    )
    tools = ToolRegistry()

    async def costly(_args):
        return ToolExecutionResult(content="team result", metadata={"model_cost_usd": 0.05})

    tools.register(
        ToolSpec(
            name="costly_team",
            description="test",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        costly,
    )
    runtime = AgentRuntime(
        config=HarnessConfig(primary=ModelRole(model="fake"), workspace=str(tmp_path)),
        provider=provider,
        tools=tools,
        traces=TraceStore(str(tmp_path / "trace.sqlite")),
        memory=MemoryStore(str(tmp_path / "memory.sqlite")),
    )
    result = await runtime.run(Goal(text="do it", max_cost_usd=0.01, max_steps=3))
    assert result.status == RunStatus.FAILED
    assert "budget exhausted" in result.answer.lower()
    assert result.reported_cost_usd == pytest.approx(0.05)

@pytest.mark.asyncio
async def test_team_declines_obviously_simple_work_without_spending_model_tokens(tmp_path: Path):
    provider = FakeProvider([])
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        team=TeamConfig(enabled=True),
    )

    async def child(_goal: Goal) -> RunResult:
        raise AssertionError("no child should be launched")

    orchestrator = TeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="What is 2+2?",
        success_criteria=[],
        constraints=[],
        max_agents=None,
        max_cost_usd=None,
    )
    assert "TEAM_NOT_JUSTIFIED" in result.content
    assert result.metadata["model_cost_usd"] == 0.0
    assert result.metadata["team_agents_executed"] == 0

@pytest.mark.asyncio
async def test_team_reopens_only_targeted_followup_when_synthesis_finds_material_gap(tmp_path: Path):
    plan = {
        "rationale": "start with one cheap specialist",
        "tasks": [
            {
                "id": "a",
                "task": "inspect initial evidence",
                "dependencies": [],
                "profile": "research",
                "difficulty": 0.2,
                "expected_value": 0.9,
                "critical": False,
                "freshness": "stable",
                "cacheable": False,
            }
        ],
    }
    first_synthesis = {
        "answer": "partial",
        "confidence": 0.61,
        "should_continue": True,
        "unresolved": ["independent confirmation missing"],
        "followups": [
            {
                "task": "independently verify the disputed point",
                "profile": "research",
                "difficulty": 0.4,
                "expected_value": 0.95,
                "freshness": "stable",
            }
        ],
    }
    final_synthesis = {
        "answer": "verified final",
        "confidence": 0.94,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider(
        [
            ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.001}),
            ModelTurn(content=json.dumps(first_synthesis), usage={"cost_usd": 0.001}),
            ModelTurn(content=json.dumps(final_synthesis), usage={"cost_usd": 0.001}),
        ]
    )
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        team=TeamConfig(enabled=True, max_agents=3, max_rounds=3),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        return RunResult(
            run_id=f"child-{len(seen)}",
            status=RunStatus.SUCCEEDED,
            answer=f"evidence {len(seen)}",
            reported_cost_usd=0.002,
        )

    orchestrator = TeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="audit a disputed technical claim with independent verification",
        success_criteria=["verify the disputed point", "state residual uncertainty"],
        constraints=[],
        max_agents=3,
        max_cost_usd=None,
    )
    assert result.content == "verified final"
    assert len(seen) == 2
    assert "independently verify" in seen[1].text
    assert result.metadata["team_rounds"] == 2
    assert result.metadata["team_agents_executed"] == 2
    assert result.metadata["model_cost_usd"] == pytest.approx(0.007)
