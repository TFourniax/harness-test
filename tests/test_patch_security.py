from adaptive_harness.improvement.patch_security import PatchSecurityAnalyzer


def test_forbidden_privilege_addition_is_rejected():
    patch = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-old
+cmd = '--privileged'
"""
    verdict = PatchSecurityAnalyzer.analyze(patch)
    assert not verdict.passed
    assert any("privilege" in r for r in verdict.reasons)


def test_normal_strategy_patch_is_allowed():
    patch = """diff --git a/src/adaptive_harness/runtime/context.py b/src/adaptive_harness/runtime/context.py
--- a/src/adaptive_harness/runtime/context.py
+++ b/src/adaptive_harness/runtime/context.py
@@ -1 +1 @@
-old
+new
"""
    assert PatchSecurityAnalyzer.analyze(patch).passed


def test_self_evolution_cannot_modify_human_channel_trust_boundary():
    patch = """diff --git a/src/adaptive_harness/channels/telegram.py b/src/adaptive_harness/channels/telegram.py
--- a/src/adaptive_harness/channels/telegram.py
+++ b/src/adaptive_harness/channels/telegram.py
@@ -1 +1 @@
-old
+new
"""
    verdict = PatchSecurityAnalyzer.analyze(patch)
    assert not verdict.passed
    assert any("root-of-trust" in reason for reason in verdict.reasons)
