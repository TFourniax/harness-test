from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from adaptive_harness.contracts import ApprovalRequest, RiskLevel, ToolCall, ToolSpec


def action_fingerprint(call: ToolCall) -> str:
    payload = {"tool": call.name, "arguments": call.arguments}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ""
    approval: ApprovalRequest | None = None


class CapabilityPolicy:
    """Enforces capabilities outside the model.

    Prompt text may request or describe permissions; only this policy grants them.
    """

    def __init__(self, allowed_scopes: set[str], approval_risks: set[str]) -> None:
        self.allowed_scopes = allowed_scopes
        self.approval_risks = approval_risks

    def evaluate(self, run_id: str, spec: ToolSpec, call: ToolCall) -> PolicyDecision:
        missing = spec.required_scopes - self.allowed_scopes
        if missing:
            return PolicyDecision(False, reason=f"missing scopes: {sorted(missing)}")

        if spec.risk.value in self.approval_risks:
            payload = {"tool": call.name, "arguments": call.arguments}
            fingerprint = action_fingerprint(call)
            approval = ApprovalRequest(
                run_id=run_id,
                action_type="tool_call",
                summary=f"Approve {spec.risk.value} tool call: {call.name}",
                payload=payload,
                fingerprint=fingerprint,
            )
            return PolicyDecision(
                False,
                requires_approval=True,
                reason="human approval required",
                approval=approval,
            )

        return PolicyDecision(True)


COMMITMENT_RISKS = {
    RiskLevel.REVERSIBLE_WRITE,
    RiskLevel.EXTERNAL_SIDE_EFFECT,
    RiskLevel.PRIVILEGED,
}
