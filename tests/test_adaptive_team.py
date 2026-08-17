import json
from pathlib import Path

import pytest

from adaptive_harness.config import HarnessConfig, ModelRole, TeamConfig
from adaptive_harness.contracts import Goal, ModelTurn, RunStatus
from adaptive_harness.orchestration.adaptive_team import AdaptiveTeamOrchestrator
from adaptive_harness.providers.fake import FakeProvider
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.trace_store import TraceStore


@pytest.mark.asyncio
async def test_medium_task_uses_diverse_cheap_pair_with_bounded_attempts(tmp_path: Path):
    plan = {
        "rationale": "one material work item",
        "tasks": [{
            "id": "a",
            "task": "analyze two plausible implementation paths",
            "dependencies": [],
            "profile": "code",
            "difficulty": 0.6,
            "expected_value": 0.9,
            "critical": False,
            "freshness": "stable",
            "cacheable": False,
        }],
    }
    synthesis = {
        "answer": "combined",
        "confidence": 0.9,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider([
        ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.001}),
        ModelTurn(content=json.dumps(synthesis), usage={"cost_usd": 0.002}),
    ])
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        max_parallel_agents=4,
        team=TeamConfig(enabled=True, max_agents=2, max_rounds=2),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        return RunResult(
            run_id=f"child-{len(seen)}",
            status=RunStatus.SUCCEEDED,
            answer=f"{goal.model_role} result",
            reported_cost_usd=0.002,
        )

    orchestrator = AdaptiveTeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite3")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="analyze and compare implementation strategies for a complex code change",
        success_criteria=["compare alternatives", "identify risks"],
        constraints=[],
        max_agents=2,
        max_cost_usd=None,
    )
    assert [goal.model_role for goal in seen] == ["cheap", "cheap"]
    assert result.metadata["team_child_attempts"] == 2
    assert result.metadata["compute_market_actions"]["cheap_pair"] == 1
    assert result.metadata["model_cost_usd"] == pytest.approx(0.007)


@pytest.mark.asyncio
async def test_critical_hard_task_uses_mixed_independent_pair(tmp_path: Path):
    plan = {
        "rationale": "critical verification",
        "tasks": [{
            "id": "a",
            "task": "verify migration safety and failure modes",
            "dependencies": [],
            "profile": "operations",
            "difficulty": 0.92,
            "expected_value": 1.0,
            "critical": True,
            "freshness": "stable",
            "cacheable": False,
        }],
    }
    synthesis = {
        "answer": "verified",
        "confidence": 0.95,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider([
        ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.001}),
        ModelTurn(content=json.dumps(synthesis), usage={"cost_usd": 0.002}),
    ])
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        max_parallel_agents=4,
        team=TeamConfig(enabled=True, max_agents=2, max_rounds=2),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        cost = 0.02 if goal.model_role == "primary" else 0.002
        return RunResult(
            run_id=f"child-{len(seen)}",
            status=RunStatus.SUCCEEDED,
            answer=f"{goal.model_role} independent check",
            reported_cost_usd=cost,
        )

    orchestrator = AdaptiveTeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite3")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="audit a critical production migration with independent verification",
        success_criteria=["prove rollback safety", "find failure modes"],
        constraints=[],
        max_agents=2,
        max_cost_usd=None,
    )
    assert sorted(goal.model_role for goal in seen) == ["cheap", "primary"]
    assert result.metadata["compute_market_actions"]["mixed_pair"] == 1
    assert result.metadata["team_child_attempts"] == 2


@pytest.mark.asyncio
async def test_budget_pressure_downshifts_mixed_pair_to_primary(tmp_path: Path):
    plan = {
        "rationale": "critical verification under a hard budget",
        "tasks": [{
            "id": "a",
            "task": "verify a critical change",
            "dependencies": [],
            "profile": "operations",
            "difficulty": 0.9,
            "expected_value": 1.0,
            "critical": True,
            "freshness": "stable",
            "cacheable": False,
        }],
    }
    synthesis = {
        "answer": "done",
        "confidence": 0.9,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider([
        ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.0}),
        ModelTurn(content=json.dumps(synthesis), usage={"cost_usd": 0.0}),
    ])
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        team=TeamConfig(enabled=True, max_agents=2),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        return RunResult(
            run_id="child",
            status=RunStatus.SUCCEEDED,
            answer="ok",
            reported_cost_usd=0.019,
        )

    orchestrator = AdaptiveTeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite3")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="audit critical migration safety",
        success_criteria=["verify safety", "state risks"],
        constraints=[],
        max_agents=2,
        max_cost_usd=0.021,
    )
    assert [goal.model_role for goal in seen] == ["primary"]
    assert result.metadata["compute_market_actions"]["primary_single"] == 1
