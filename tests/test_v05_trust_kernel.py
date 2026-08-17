from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION


def test_evidence_and_compute_policy_boundaries_are_not_self_promotable():
    protected = set(IMMUTABLE_FROM_SELF_EVOLUTION)
    assert "src/adaptive_harness/tools/evidence_tools.py" in protected
    assert "src/adaptive_harness/orchestration/verification.py" in protected
    assert "src/adaptive_harness/orchestration/confidence.py" in protected
    assert "src/adaptive_harness/orchestration/compute_market.py" in protected
    assert "src/adaptive_harness/orchestration/policy_arena.py" in protected
    assert "src/adaptive_harness/orchestration/benchmark.py" in protected
