from pathlib import Path

from adaptive_harness.config import ComputeEconomyConfig
from adaptive_harness.orchestration.policy_arena import PolicyArenaStore
from adaptive_harness.orchestration.policy_lab import PolicyUncertaintyLab


def _arena(tmp_path: Path) -> PolicyArenaStore:
    cfg = ComputeEconomyConfig(
        policy_min_matched_trials=12,
        policy_evidence_floor=0.6,
    )
    return PolicyArenaStore(tmp_path / "arena.sqlite3", config=cfg)


def test_unmatched_live_data_is_ignored_by_uncertainty_lab(tmp_path: Path):
    arena = _arena(tmp_path)
    champion = arena.champion().id
    challenger = "frugal-v1"
    for index in range(30):
        arena.record_trial(
            policy_id=challenger,
            task_key=f"live-{index}",
            success=True,
            quality=1.0,
            cost_usd=0.001,
            source="live",
            evidence_strength=1.0,
        )
    report = PolicyUncertaintyLab(
        tmp_path / "arena.sqlite3", bootstrap_samples=300
    ).compare(champion_id=champion, challenger_id=challenger)
    assert report.paired_tasks == 0
    assert report.recommendation == "insufficient"


def test_paired_heldout_cost_reduction_can_be_robustly_promotable(tmp_path: Path):
    arena = _arena(tmp_path)
    champion = arena.champion().id
    challenger = "frugal-v1"
    for index in range(20):
        key = f"case-{index}"
        arena.record_trial(
            policy_id=champion,
            task_key=key,
            success=True,
            quality=0.90,
            cost_usd=0.020,
            source="benchmark",
            evidence_strength=0.9,
        )
        arena.record_trial(
            policy_id=challenger,
            task_key=key,
            success=True,
            quality=0.902,
            cost_usd=0.014,
            source="benchmark",
            evidence_strength=0.9,
        )
    report = PolicyUncertaintyLab(
        tmp_path / "arena.sqlite3", bootstrap_samples=500
    ).compare(
        champion_id=champion,
        challenger_id=challenger,
        cost_reduction_target=0.10,
    )
    assert report.paired_tasks == 20
    assert report.robust_quality_noninferior
    assert report.robust_cost_reduction
    assert report.recommendation == "promotable"
    assert len(report.fingerprint) == 64


def test_noisy_quality_tradeoff_is_held_when_interval_crosses_regression(tmp_path: Path):
    arena = _arena(tmp_path)
    champion = arena.champion().id
    challenger = "quality-v1"
    for index in range(20):
        key = f"case-{index}"
        challenger_quality = 0.94 if index % 2 == 0 else 0.84
        arena.record_trial(
            policy_id=champion,
            task_key=key,
            success=True,
            quality=0.90,
            cost_usd=0.02,
            source="benchmark",
            evidence_strength=0.9,
        )
        arena.record_trial(
            policy_id=challenger,
            task_key=key,
            success=challenger_quality >= 0.88,
            quality=challenger_quality,
            cost_usd=0.021,
            source="benchmark",
            evidence_strength=0.9,
        )
    report = PolicyUncertaintyLab(
        tmp_path / "arena.sqlite3", bootstrap_samples=800
    ).compare(champion_id=champion, challenger_id=challenger)
    assert report.paired_tasks == 20
    assert not report.robust_quality_gain
    assert report.recommendation == "hold"


def test_report_fingerprint_changes_when_paired_evidence_changes(tmp_path: Path):
    arena = _arena(tmp_path)
    champion = arena.champion().id
    challenger = "frugal-v1"
    for index in range(12):
        key = f"case-{index}"
        for policy_id, quality, cost in (
            (champion, 0.90, 0.02),
            (challenger, 0.91, 0.015),
        ):
            arena.record_trial(
                policy_id=policy_id,
                task_key=key,
                success=True,
                quality=quality,
                cost_usd=cost,
                source="benchmark",
                evidence_strength=0.9,
            )
    lab = PolicyUncertaintyLab(tmp_path / "arena.sqlite3", bootstrap_samples=300)
    first = lab.compare(champion_id=champion, challenger_id=challenger)
    arena.record_trial(
        policy_id=challenger,
        task_key="case-0",
        success=False,
        quality=0.2,
        cost_usd=0.05,
        source="benchmark",
        evidence_strength=0.9,
    )
    second = lab.compare(champion_id=champion, challenger_id=challenger)
    assert first.fingerprint != second.fingerprint
