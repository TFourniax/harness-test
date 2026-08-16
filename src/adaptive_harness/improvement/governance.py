from __future__ import annotations

import hashlib

from adaptive_harness.contracts import ImprovementProposal
from adaptive_harness.improvement.patch_security import PatchSecurityAnalyzer


class PromotionGate:
    """Final non-model gate for self-modification promotion."""

    @staticmethod
    def promotable(proposal: ImprovementProposal) -> tuple[bool, str]:
        if proposal.human_status != "approved":
            return False, "human approval missing"
        if not proposal.regression_passed:
            return False, "regression suite failed"
        if not proposal.security_passed:
            return False, "security suite failed"
        if not proposal.patch.strip():
            return False, "empty patch"
        security = PatchSecurityAnalyzer.analyze(proposal.patch)
        if not security.passed:
            return False, "; ".join(security.reasons)
        return True, "ok"

    @staticmethod
    def fingerprint(proposal: ImprovementProposal) -> str:
        return hashlib.sha256(proposal.patch.encode()).hexdigest()
