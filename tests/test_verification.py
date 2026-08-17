from adaptive_harness.contracts import Observation, RunStatus, TrustLevel
from adaptive_harness.orchestration.contracts import VerificationVerdict, WorkItem
from adaptive_harness.orchestration.verification import VerificationEngine
from adaptive_harness.runtime.agent import RunResult


def _result(*observations: Observation) -> RunResult:
    return RunResult(
        run_id="r",
        status=RunStatus.SUCCEEDED,
        answer="answer",
        observations=list(observations),
    )


def test_explicit_deterministic_postcondition_produces_strong_certificate():
    obs = Observation(
        call_id="v1",
        tool_name="verify_workspace_command",
        ok=True,
        content='{"returncode": 0}',
        metadata={
            "verification_signal": "deterministic_pass",
            "verification_kind": "tests",
            "verification_claim": "pytest suite passes",
        },
        trust=TrustLevel.TOOL,
    )
    cert = VerificationEngine().certify(
        WorkItem(id="x", task="validate code", profile="code"), [_result(obs)]
    )
    assert cert.verdict == VerificationVerdict.VERIFIED
    assert cert.deterministic
    assert cert.evidence_strength >= 0.95
    assert cert.learning_success == 1.0


def test_failed_explicit_postcondition_refutes_claim():
    obs = Observation(
        call_id="v1",
        tool_name="verify_workspace_command",
        ok=True,
        content='{"returncode": 1}',
        metadata={
            "verification_signal": "deterministic_fail",
            "verification_kind": "tests",
            "verification_claim": "pytest suite passes",
        },
        trust=TrustLevel.TOOL,
    )
    cert = VerificationEngine().certify(
        WorkItem(id="x", task="validate code", profile="code"), [_result(obs)]
    )
    assert cert.verdict == VerificationVerdict.REFUTED
    assert cert.evidence_strength >= 0.85
    assert cert.learning_success == 0.0


def test_two_independent_source_hosts_support_but_do_not_deterministically_verify():
    observations = [
        Observation(
            call_id=f"s{i}",
            tool_name="source_fetch",
            ok=True,
            content="source text",
            metadata={"source_host": host, "verification_signal": "source_observation"},
            trust=TrustLevel.UNTRUSTED_EXTERNAL,
        )
        for i, host in enumerate(("a.example", "b.example"), start=1)
    ]
    cert = VerificationEngine().certify(
        WorkItem(id="x", task="research claim", profile="research"), [_result(*observations)]
    )
    assert cert.verdict == VerificationVerdict.SUPPORTED
    assert cert.independent_sources == 2
    assert 0.5 <= cert.evidence_strength < 1.0
    assert not cert.deterministic


def test_model_only_success_has_tiny_learning_weight():
    cert = VerificationEngine().certify(
        WorkItem(id="x", task="reason", profile="generic"), [_result()]
    )
    assert cert.verdict == VerificationVerdict.UNVERIFIED
    assert cert.evidence_strength <= 0.10


def test_unrelated_deterministic_check_cannot_launder_research_claim_into_verified():
    obs = Observation(
        call_id="v1",
        tool_name="verify_workspace_command",
        ok=True,
        content='{"returncode": 0}',
        metadata={
            "verification_signal": "deterministic_pass",
            "verification_kind": "compile",
            "verification_claim": "python files compile",
        },
        trust=TrustLevel.TOOL,
    )
    cert = VerificationEngine().certify(
        WorkItem(id="x", task="research the current market share", profile="research"),
        [_result(obs)],
    )
    assert cert.verdict != VerificationVerdict.VERIFIED
    assert cert.scope_coverage < 0.5
    assert cert.evidence_strength < 0.72
