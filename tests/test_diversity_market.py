from pathlib import Path

import pytest

from adaptive_harness.orchestration.contracts import (
    VerificationCertificate,
    VerificationVerdict,
)
from adaptive_harness.orchestration.diversity_market import (
    IndependenceScorer,
    MarginalDiversityMarket,
    MarginalDiversityStore,
    PanelAttempt,
    evidence_first_select,
)


def cert(
    verdict=VerificationVerdict.UNVERIFIED,
    *,
    strength=0.08,
    score=0.5,
    coverage=0.0,
    deterministic=False,
    sources=0,
):
    return VerificationCertificate(
        verdict=verdict,
        evidence_strength=strength,
        score=score,
        scope_coverage=coverage,
        deterministic=deterministic,
        independent_sources=sources,
    )


def attempt(
    answer: str,
    *,
    method="direct",
    role="cheap",
    model="cheap/a",
    refs=None,
    verification=None,
    cost=0.002,
):
    return PanelAttempt(
        method_id=method,
        model_role=role,
        model_id=model,
        answer=answer,
        evidence_refs=refs or [],
        verification=verification or cert(),
        cost_usd=cost,
    )


def test_different_prose_without_evidence_is_not_treated_as_strong_independence():
    scorer = IndependenceScorer()
    a = attempt("The parser likely fails because the branch order is wrong.")
    b = attempt(
        "A completely different hypothesis is that token normalization corrupts state.",
        method="counterexample",
        model="cheap/b",
    )
    result = scorer.pair(b, a)
    assert result.evidence_novelty == 0.0
    assert result.score < 0.5


def test_repeating_same_evidence_is_penalized_even_with_different_method_and_model():
    scorer = IndependenceScorer()
    a = attempt("Tests show auth failure.", refs=["test-1", "log-1"])
    b = attempt(
        "Independent review confirms auth failure.",
        refs=["test-1", "log-1"],
        method="falsify",
        role="primary",
        model="primary/a",
    )
    result = scorer.pair(b, a)
    assert result.evidence_novelty == 0.0
    assert result.score < 0.6


def test_new_concrete_evidence_dominates_independence_score():
    scorer = IndependenceScorer()
    a = attempt("Primary source A supports claim.", refs=["source-a"])
    b = attempt(
        "Independent source B and deterministic check support claim.",
        refs=["source-b", "check-b"],
        method="disconfirm",
        role="primary",
        model="primary/a",
    )
    result = scorer.pair(b, a)
    assert result.evidence_novelty == 1.0
    assert result.score >= 0.65


def test_against_panel_is_conservative_not_max_pair_diversity():
    scorer = IndependenceScorer()
    prior = [
        attempt("A", refs=["a"], method="direct"),
        attempt("B", refs=["b"], method="counterexample"),
    ]
    candidate = attempt(
        "Mostly repeats A",
        refs=["a"],
        method="third-method",
        role="primary",
    )
    result = scorer.against_panel(candidate, prior)
    assert result.evidence_novelty == 0.0
    assert result.score < 0.6


def test_evidence_first_selector_ignores_majority_vote_and_prefers_verified_attempt():
    weak_a = attempt("Consensus answer", verification=cert(), cost=0.001)
    weak_b = attempt("Consensus answer", method="alt", verification=cert(), cost=0.001)
    strong = attempt(
        "Minority answer with deterministic proof",
        method="verify",
        role="primary",
        verification=cert(
            VerificationVerdict.VERIFIED,
            strength=0.96,
            score=0.97,
            coverage=0.94,
            deterministic=True,
        ),
        cost=0.02,
    )
    assert evidence_first_select([weak_a, weak_b, strong]) is strong


def test_market_stops_when_current_certificate_is_already_strongly_verified(tmp_path: Path):
    market = MarginalDiversityMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    decision = market.decide(
        profile="code",
        difficulty=0.9,
        critical=True,
        slot_index=2,
        current_certificate=cert(
            VerificationVerdict.VERIFIED,
            strength=0.96,
            score=0.98,
            coverage=0.94,
            deterministic=True,
        ),
        expected_cost_usd=0.002,
        remaining_budget_usd=0.1,
    )
    assert not decision.buy
    assert "already" in decision.reason


def test_market_never_buys_attempt_that_remaining_escrow_cannot_fund(tmp_path: Path):
    market = MarginalDiversityMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    decision = market.decide(
        profile="research",
        difficulty=0.8,
        critical=False,
        slot_index=2,
        current_certificate=cert(),
        expected_cost_usd=0.02,
        remaining_budget_usd=0.01,
    )
    assert not decision.buy
    assert "cannot fund" in decision.reason


def test_repeated_redundant_second_attempts_train_market_to_stop(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(store, min_samples=4, redundancy_floor=0.25)
    bucket = market.bucket("code", 0.8, False)
    for _ in range(5):
        store.record(
            bucket=bucket,
            slot_index=2,
            method_id="counterexample",
            independence=0.10,
            marginal_evidence_gain=0.01,
            marginal_score_gain=0.0,
            marginal_coverage_gain=0.0,
            cost_usd=0.004,
            selected=False,
        )
    decision = market.decide(
        profile="code",
        difficulty=0.8,
        critical=False,
        slot_index=2,
        current_certificate=cert(),
        expected_cost_usd=0.004,
        remaining_budget_usd=0.1,
    )
    assert not decision.buy
    assert decision.sample_count == 5
    assert "redundant" in decision.reason


def test_repeated_high_value_independent_attempts_remain_worth_buying(tmp_path: Path):
    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(store, min_samples=4, min_utility=0.02, cost_weight=2.0)
    bucket = market.bucket("research", 0.85, True)
    for _ in range(5):
        store.record(
            bucket=bucket,
            slot_index=2,
            method_id="independent-source",
            independence=0.82,
            marginal_evidence_gain=0.24,
            marginal_score_gain=0.18,
            marginal_coverage_gain=0.20,
            cost_usd=0.006,
            selected=True,
        )
    decision = market.decide(
        profile="research",
        difficulty=0.85,
        critical=True,
        slot_index=2,
        current_certificate=cert(),
        expected_cost_usd=0.006,
        remaining_budget_usd=0.1,
    )
    assert decision.buy
    assert decision.expected_independence > 0.7
    assert decision.utility > 0.02


def test_third_attempt_has_lower_cold_start_prior_than_second(tmp_path: Path):
    market = MarginalDiversityMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    second = market.decide(
        profile="generic",
        difficulty=0.8,
        critical=False,
        slot_index=2,
        current_certificate=cert(),
        expected_cost_usd=0.0,
        remaining_budget_usd=1.0,
    )
    third = market.decide(
        profile="generic",
        difficulty=0.8,
        critical=False,
        slot_index=3,
        current_certificate=cert(),
        expected_cost_usd=0.0,
        remaining_budget_usd=1.0,
    )
    assert third.expected_gain < second.expected_gain
    assert third.expected_independence < second.expected_independence
