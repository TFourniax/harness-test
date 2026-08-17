from pathlib import Path

import pytest

from adaptive_harness.orchestration.policy_arena import PolicyArenaStore


def test_shadow_or_live_observations_cannot_self_promote_policy(tmp_path: Path):
    arena = PolicyArenaStore(tmp_path / "arena.sqlite3", min_matched_trials=2)
    for i in range(5):
        arena.record_trial(
            policy_id="frugal-v1",
            task_key=f"t{i}",
            quality=1.0,
            cost_usd=0.001,
            evidence_strength=1.0,
            passed=True,
            source="shadow",
        )
    comparison = arena.compare("frugal-v1")
    assert not comparison.promotable
    assert comparison.matched_trials == 0


def _seed_promotable(arena: PolicyArenaStore, count: int = 4) -> None:
    for i in range(count):
        key = f"case-{i}"
        arena.record_trial(
            policy_id="balanced-v1",
            task_key=key,
            quality=0.90,
            cost_usd=0.020,
            evidence_strength=0.90,
            passed=True,
            source="benchmark",
        )
        arena.record_trial(
            policy_id="frugal-v1",
            task_key=key,
            quality=0.895,
            cost_usd=0.012,
            evidence_strength=0.90,
            passed=True,
            source="benchmark",
        )


def test_matched_high_evidence_benchmark_can_recommend_cheaper_non_regressing_policy(tmp_path: Path):
    arena = PolicyArenaStore(tmp_path / "arena.sqlite3", min_matched_trials=4)
    _seed_promotable(arena)
    comparison = arena.compare("frugal-v1")
    assert comparison.promotable
    assert comparison.challenger_cost < comparison.champion_cost
    assert len(comparison.fingerprint) == 64


def test_promotion_requires_exact_current_benchmark_fingerprint(tmp_path: Path):
    arena = PolicyArenaStore(tmp_path / "arena.sqlite3", min_matched_trials=4)
    _seed_promotable(arena)
    comparison = arena.compare("frugal-v1")
    with pytest.raises(ValueError, match="fingerprint"):
        arena.promote("frugal-v1", approved_fingerprint="wrong")
    arena.promote("frugal-v1", approved_fingerprint=comparison.fingerprint)
    assert arena.champion().id == "frugal-v1"


def test_stale_policy_approval_is_invalid_after_benchmark_evidence_changes(tmp_path: Path):
    arena = PolicyArenaStore(tmp_path / "arena.sqlite3", min_matched_trials=4)
    _seed_promotable(arena)
    stale = arena.compare("frugal-v1").fingerprint
    # Latest matched benchmark observation changes the exact comparison under review.
    arena.record_trial(
        policy_id="frugal-v1",
        task_key="case-0",
        quality=0.90,
        cost_usd=0.011,
        evidence_strength=0.95,
        passed=True,
        source="benchmark",
    )
    assert arena.compare("frugal-v1").fingerprint != stale
    with pytest.raises(ValueError, match="fingerprint"):
        arena.promote("frugal-v1", approved_fingerprint=stale)
