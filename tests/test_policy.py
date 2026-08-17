from adaptive_harness.contracts import RiskLevel, ToolCall, ToolSpec
from adaptive_harness.runtime.policy import CapabilityPolicy


def test_missing_scope_blocks():
    p = CapabilityPolicy({"fs:read"}, {"external_side_effect", "privileged"})
    spec = ToolSpec(
        name="write",
        description="x",
        input_schema={"type": "object"},
        risk=RiskLevel.REVERSIBLE_WRITE,
        required_scopes={"fs:write"},
    )
    d = p.evaluate("r", spec, ToolCall(name="write"))
    assert not d.allowed
    assert "fs:write" in d.reason


def test_external_side_effect_requires_human_approval():
    p = CapabilityPolicy({"mail:send"}, {"external_side_effect", "privileged"})
    spec = ToolSpec(
        name="send_mail",
        description="x",
        input_schema={"type": "object"},
        risk=RiskLevel.EXTERNAL_SIDE_EFFECT,
        required_scopes={"mail:send"},
    )
    call = ToolCall(name="send_mail", arguments={"to": "a@example.com"})
    d = p.evaluate("r", spec, call)
    assert d.requires_approval
    assert d.approval is not None
    assert d.approval.fingerprint
