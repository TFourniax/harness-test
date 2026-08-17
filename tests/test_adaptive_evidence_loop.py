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
async def test_high_raw_confidence_without_evidence_reopens_one_bounded_verification_round(tmp_path: Path):
    plan = {
        "rationale": "start cheap",
        "tasks": [{
            "id": "a",
            "task": "inspect a modest implementation claim",
            "dependencies": [],
            "profile": "code",
            "difficulty": 0.2,
            "expected_value": 0.9,
            "critical": False,
            "freshness": "stable",
            "cacheable": False,
        }],
    }
    synthesis1 = {
        "answer": "very confident but unverified",
        "confidence": 0.96,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    synthesis2 = {
        "answer": "final after verification attempt",
        "confidence": 0.94,
        "should_continue": False,
        "unresolved": [],
        "followups": [],
    }
    provider = FakeProvider([
        ModelTurn(content=json.dumps(plan), usage={"cost_usd": 0.001}),
        ModelTurn(content=json.dumps(synthesis1), usage={"cost_usd": 0.001}),
        ModelTurn(content=json.dumps(synthesis2), usage={"cost_usd": 0.001}),
    ])
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        workspace=str(tmp_path),
        harness_root=str(tmp_path),
        team=TeamConfig(enabled=True, max_agents=2, max_rounds=2),
    )
    seen: list[Goal] = []

    async def child(goal: Goal) -> RunResult:
        seen.append(goal)
        return RunResult(
            run_id=f"child-{len(seen)}",
            status=RunStatus.SUCCEEDED,
            answer="model-only evidence",
            reported_cost_usd=0.001,
        )

    orchestrator = AdaptiveTeamOrchestrator(
        config=cfg,
        provider=provider,
        traces=TraceStore(str(tmp_path / "trace.sqlite3")),
        child_runner=child,
        cache=None,
    )
    result = await orchestrator.execute(
        root_goal="analyze a code implementation and verify the conclusion",
        success_criteria=["give a conclusion", "verify material assumptions"],
        constraints=[],
        max_agents=2,
        max_cost_usd=None,
    )
    assert len(seen) == 2
    assert "Independently verify" in seen[1].text
    assert result.metadata["team_rounds"] == 2
    assert result.metadata["team_raw_confidence"] == pytest.approx(0.94)
    assert result.metadata["team_confidence"] < result.metadata["team_raw_confidence"]
