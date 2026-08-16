from adaptive_harness.contracts import ImprovementProposal
from adaptive_harness.improvement.governance import PromotionGate


def test_human_gate_is_mandatory():
    p = ImprovementProposal(
        title="x",
        weakness="x",
        hypothesis="x",
        patch="diff --git a/a b/a\n",
        target_files=["a"],
        regression_passed=True,
        security_passed=True,
    )
    ok, reason = PromotionGate.promotable(p)
    assert not ok
    assert "human" in reason
    p.human_status = "approved"
    assert PromotionGate.promotable(p)[0]


def test_self_evolution_cannot_rewrite_its_exam_or_policy():
    for protected in [
        "tests/test_runtime.py",
        "evals/suites/core.yaml",
        "src/adaptive_harness/runtime/policy.py",
        "src/adaptive_harness/improvement/governance.py",
    ]:
        patch = f"diff --git a/{protected} b/{protected}\n--- a/{protected}\n+++ b/{protected}\n@@ -1 +1 @@\n-old\n+new\n"
        p = ImprovementProposal(
            title="cheat",
            weakness="x",
            hypothesis="x",
            patch=patch,
            target_files=[protected],
            regression_passed=True,
            security_passed=True,
            human_status="approved",
        )
        ok, reason = PromotionGate.promotable(p)
        assert not ok
        assert "root-of-trust" in reason
