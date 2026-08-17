from adaptive_harness.orchestration.confidence import TrajectoryConfidenceCalibrator
from adaptive_harness.orchestration.contracts import (
    AgentReport,
    VerificationCertificate,
    VerificationVerdict,
)


def test_high_model_confidence_is_capped_without_evidence():
    report = AgentReport(
        task_id="x",
        answer="confident answer",
        confidence=0.76,
        verification=VerificationCertificate(
            verdict=VerificationVerdict.UNVERIFIED,
            evidence_strength=0.08,
            score=0.5,
        ),
    )
    estimate = TrajectoryConfidenceCalibrator().calibrate(
        raw_confidence=0.99, profile="research", reports=[report]
    )
    assert estimate.calibrated <= 0.78


def test_strong_deterministic_evidence_can_lift_calibrated_confidence():
    report = AgentReport(
        task_id="x",
        answer="tested",
        confidence=0.94,
        verification=VerificationCertificate(
            verdict=VerificationVerdict.VERIFIED,
            evidence_strength=1.0,
            score=1.0,
            deterministic=True,
        ),
    )
    estimate = TrajectoryConfidenceCalibrator().calibrate(
        raw_confidence=0.86, profile="code", reports=[report]
    )
    assert estimate.calibrated >= 0.94
    assert estimate.deterministic_verified


def test_refutation_caps_confidence_even_if_synthesizer_is_confident():
    report = AgentReport(
        task_id="x",
        answer="claim",
        confidence=0.18,
        verification=VerificationCertificate(
            verdict=VerificationVerdict.REFUTED,
            evidence_strength=1.0,
            score=0.0,
            deterministic=True,
        ),
    )
    estimate = TrajectoryConfidenceCalibrator().calibrate(
        raw_confidence=0.98, profile="code", reports=[report]
    )
    assert estimate.calibrated <= 0.22
    assert estimate.refuted
