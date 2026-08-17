from adaptive_harness.contracts import EvidenceRequirement, RiskLevel, ToolCall, ToolSpec
from adaptive_harness.runtime.evidence import EvidenceGate


def test_commitment_blocked_until_evidence():
    req = EvidenceRequirement(id="tests", description="Inspect tests", applies_to=["fs_write"])
    gate = EvidenceGate([req])
    spec = ToolSpec(
        name="fs_write",
        description="x",
        input_schema={"type": "object"},
        risk=RiskLevel.REVERSIBLE_WRITE,
    )
    gaps = gate.relevant_gaps(spec, ToolCall(name="fs_write"))
    assert [g.id for g in gaps] == ["tests"]
