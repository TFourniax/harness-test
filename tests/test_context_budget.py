from pathlib import Path

from adaptive_harness.orchestration.context_budget import (
    ContextBudgetAllocator,
    ContextCandidate,
    MissionContextBuilder,
)
from adaptive_harness.orchestration.contracts import VerificationCertificate, VerificationVerdict
from adaptive_harness.orchestration.state_plane import TaskStateStore


def test_required_context_is_kept_before_optional_material():
    allocator = ContextBudgetAllocator()
    result = allocator.allocate(
        [
            ContextCandidate(
                key="required",
                kind="mission",
                text="root mission and exact current revision",
                required=True,
                priority=1,
                relevance=1,
                evidence_strength=1,
            ),
            ContextCandidate(
                key="noise",
                kind="history",
                text="old unrelated material " * 100,
                priority=0.1,
            ),
        ],
        query="root mission",
        max_tokens=64,
    )
    assert result.selected_keys[0] == "required"
    assert result.used_tokens <= result.budget_tokens


def test_evidence_and_relevance_win_under_tight_budget():
    allocator = ContextBudgetAllocator()
    candidates = [
        ContextCandidate(
            key="verified",
            kind="fact",
            text="payment parser regression evidence pytest verified",
            priority=0.8,
            relevance=0.9,
            evidence_strength=0.95,
        ),
        ContextCandidate(
            key="weak",
            kind="fact",
            text="payment parser speculative note without evidence",
            priority=0.8,
            relevance=0.8,
            evidence_strength=0.05,
        ),
    ]
    result = allocator.allocate(
        candidates, query="payment parser regression", max_tokens=20
    )
    assert "verified" in result.selected_keys


def test_redundant_optional_context_does_not_force_budget_overflow():
    allocator = ContextBudgetAllocator()
    candidates = [
        ContextCandidate(
            key=f"dup-{index}",
            kind="fact",
            text="same repeated parser fact " * 5,
            relevance=0.8,
        )
        for index in range(20)
    ]
    result = allocator.allocate(candidates, query="parser fact", max_tokens=80)
    assert result.used_tokens <= 80
    assert result.omitted_count > 0


def test_mission_context_contains_goal_ready_task_and_strong_fact(tmp_path: Path):
    store = TaskStateStore(tmp_path / "state.sqlite3")
    mission = store.create_mission(
        goal="repair payment parser", success_criteria=["tests pass"]
    )
    mission = store.add_task(
        mission_id=mission.id,
        task_id="repair",
        description="fix payment parser tests",
        priority=1.0,
        expected_revision=mission.revision,
    )
    cert = VerificationCertificate(
        verdict=VerificationVerdict.SUPPORTED,
        evidence_strength=0.72,
        score=0.75,
        independent_sources=3,
        evidence_refs=["a", "b", "c"],
    )
    mission = store.record_fact(
        mission_id=mission.id,
        claim="regression began after parser migration",
        certificate=cert,
        expected_revision=mission.revision,
    )
    packed = MissionContextBuilder().build(
        mission, focus_task_id="repair", max_tokens=400
    )
    assert "repair payment parser" in packed.text
    assert "fix payment parser tests" in packed.text
    assert "regression began after parser migration" in packed.text
    assert packed.used_tokens <= 400
