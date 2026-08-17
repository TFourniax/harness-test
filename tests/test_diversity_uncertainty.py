from pathlib import Path

import pytest

from adaptive_harness.orchestration.contracts import VerificationCertificate
from adaptive_harness.orchestration.diversity_market import (
    MarginalDiversityMarket,
    MarginalDiversityStore,
)


def _record(store, bucket, gains, independences, *, cost=0.004):
    for index, (gain, independence) in enumerate(zip(gains, independences)):
        store.record(
            bucket=bucket,
            slot_index=2,
            method_id=f"m-{index}",
            independence=independence,
            marginal_evidence_gain=gain,
            marginal_score_gain=gain,
            marginal_coverage_gain=gain,
            cost_usd=cost,
            selected=gain > 0,
        )


def test_store_exposes_one_sided_lower_bounds_below_noisy_means(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    bucket = "code:hard:normal"
    _record(store, bucket, [0.0, 0.0, 0.5, 0.5, 0.5], [0.2, 0.2, 0.9, 0.9, 0.9])
    stats = store.stats(bucket, 2)
    assert stats is not None
    assert stats.verification_gain_lcb < stats.verification_gain
    assert stats.independence_lcb < stats.independence
    assert 0 <= stats.verification_gain_lcb <= 1
    assert 0 <= stats.independence_lcb <= 1


def test_noisy_positive_mean_can_still_fail_robust_purchase_gate(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(
        store, min_samples=5, min_utility=0.12, cost_weight=3.0, redundancy_floor=0.1
    )
    bucket = market.bucket("code", 0.8, False)
    _record(store, bucket, [0.0, 0.0, 0.5, 0.5, 0.5], [0.2, 0.2, 0.9, 0.9, 0.9])
    decision = market.decide(
        profile="code",
        difficulty=0.8,
        critical=False,
        slot_index=2,
        current_certificate=VerificationCertificate(),
        expected_cost_usd=0.004,
        remaining_budget_usd=0.1,
    )
    assert decision.sample_count == 5
    assert not decision.buy
    assert decision.expected_gain < 0.2


def test_consistently_high_marginal_value_clears_lower_bound_gate(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(
        store, min_samples=5, min_utility=0.12, cost_weight=3.0, redundancy_floor=0.1
    )
    bucket = market.bucket("research", 0.85, True)
    _record(store, bucket, [0.28] * 6, [0.82] * 6, cost=0.005)
    decision = market.decide(
        profile="research",
        difficulty=0.85,
        critical=True,
        slot_index=2,
        current_certificate=VerificationCertificate(),
        expected_cost_usd=0.005,
        remaining_budget_usd=0.1,
    )
    assert decision.buy
    assert decision.expected_gain == pytest.approx(0.28)
    assert decision.expected_independence == pytest.approx(0.82)


def test_empirical_cost_cannot_overrun_remaining_escrow_after_learning(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(store, min_samples=3, min_utility=0.0, cost_weight=0.0)
    bucket = market.bucket("research", 0.8, False)
    _record(store, bucket, [0.4] * 4, [0.9] * 4, cost=0.02)
    decision = market.decide(
        profile="research",
        difficulty=0.8,
        critical=False,
        slot_index=2,
        current_certificate=VerificationCertificate(),
        expected_cost_usd=0.002,
        remaining_budget_usd=0.01,
    )
    assert not decision.buy
    assert decision.expected_cost_usd == pytest.approx(0.02)
    assert "empirical" in decision.reason
