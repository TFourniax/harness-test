from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.orchestration.contracts import AgentReport, VerificationVerdict


@dataclass(frozen=True)
class ConfidenceEstimate:
    raw: float
    calibrated: float
    evidence_strength: float
    deterministic_verified: bool
    refuted: bool
    reasons: tuple[str, ...]


class ConfidenceCalibrationStore:
    """Optional ground-truth reliability memory.

    The runtime does not invent labels for this store. ``record_outcome`` is intended for future
    held-out evals or externally verified outcomes. Until those labels exist, calibration remains a
    conservative trajectory heuristic rather than self-training on model confidence.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS confidence_bins (
              profile TEXT NOT NULL,
              confidence_bin INTEGER NOT NULL,
              samples INTEGER NOT NULL DEFAULT 0,
              successes INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(profile, confidence_bin)
            )
            """
        )
        self.db.commit()

    @staticmethod
    def bucket(confidence: float) -> int:
        return min(9, max(0, int(float(confidence) * 10)))

    def record_outcome(self, *, profile: str, raw_confidence: float, succeeded: bool) -> None:
        self.db.execute(
            """
            INSERT INTO confidence_bins(profile, confidence_bin, samples, successes)
            VALUES(?,?,1,?)
            ON CONFLICT(profile, confidence_bin) DO UPDATE SET
              samples=samples+1,
              successes=successes+excluded.successes
            """,
            (profile or "generic", self.bucket(raw_confidence), int(succeeded)),
        )
        self.db.commit()

    def empirical(self, *, profile: str, raw_confidence: float) -> tuple[int, float] | None:
        row = self.db.execute(
            """
            SELECT samples, successes FROM confidence_bins
            WHERE profile=? AND confidence_bin=?
            """,
            (profile or "generic", self.bucket(raw_confidence)),
        ).fetchone()
        if not row:
            return None
        samples, successes = int(row[0]), int(row[1])
        # Beta(2,2) shrinkage avoids certainty from tiny bins.
        return samples, (successes + 2.0) / (samples + 4.0)


class TrajectoryConfidenceCalibrator:
    """Conservative process-aware confidence for stop/continue decisions.

    This intentionally prevents a high self-reported synthesis confidence from becoming a strong
    stop signal when the trajectory contains little evidence. Strong deterministic postconditions
    can lift confidence; explicit refutation caps it. An empirical calibration table can later be
    blended in only when real outcome labels exist.
    """

    def __init__(
        self,
        store: ConfidenceCalibrationStore | None = None,
        *,
        min_empirical_samples: int = 12,
    ) -> None:
        self.store = store
        self.min_empirical_samples = min_empirical_samples

    @staticmethod
    def aggregate_evidence(reports: list[AgentReport]) -> tuple[float, bool, bool]:
        certs = [report.verification for report in reports if report.verification is not None]
        if not certs:
            return 0.0, False, False
        # Multiple independent pieces accumulate, but at diminishing returns.
        residual = 1.0
        deterministic_verified = False
        refuted = False
        for cert in certs:
            strength = max(0.0, min(1.0, cert.evidence_strength))
            residual *= 1.0 - 0.60 * strength
            deterministic_verified = deterministic_verified or (
                cert.deterministic and cert.verdict == VerificationVerdict.VERIFIED
            )
            refuted = refuted or cert.verdict == VerificationVerdict.REFUTED
        return min(1.0, 1.0 - residual), deterministic_verified, refuted

    def calibrate(
        self,
        *,
        raw_confidence: float,
        profile: str,
        reports: list[AgentReport],
        unresolved_count: int = 0,
    ) -> ConfidenceEstimate:
        raw = max(0.0, min(1.0, float(raw_confidence)))
        evidence, deterministic_verified, refuted = self.aggregate_evidence(reports)
        failed = sum(1 for report in reports if report.status == "failed")
        attempted = max(1, sum(max(1, report.attempt_count) for report in reports))
        failure_ratio = min(1.0, failed / attempted)

        # Shrink self-confidence toward 0.5 when evidence is weak.
        slope = 0.52 + 0.36 * evidence
        calibrated = 0.5 + (raw - 0.5) * slope
        calibrated += 0.12 * evidence
        calibrated -= min(0.20, 0.05 * max(0, unresolved_count))
        calibrated -= 0.16 * failure_ratio
        reasons = [f"trajectory_evidence={evidence:.3f}"]

        if deterministic_verified:
            calibrated = max(calibrated, min(0.98, 0.88 + 0.08 * raw))
            reasons.append("deterministic postcondition verified")
        elif evidence < 0.20:
            calibrated = min(calibrated, 0.78)
            reasons.append("model confidence capped because evidence is weak")

        if refuted:
            calibrated = min(calibrated, 0.22)
            reasons.append("explicit deterministic postcondition refuted")

        if self.store is not None:
            empirical = self.store.empirical(profile=profile, raw_confidence=raw)
            if empirical is not None and empirical[0] >= self.min_empirical_samples:
                samples, reliability = empirical
                # Empirical real-outcome calibration dominates the heuristic as evidence accumulates.
                weight = min(0.75, 0.35 + math.log1p(samples) / 12.0)
                calibrated = (1.0 - weight) * calibrated + weight * reliability
                reasons.append(
                    f"blended empirical reliability={reliability:.3f} over {samples} labeled outcomes"
                )

        calibrated = max(0.0, min(0.995, calibrated))
        return ConfidenceEstimate(
            raw=raw,
            calibrated=calibrated,
            evidence_strength=evidence,
            deterministic_verified=deterministic_verified,
            refuted=refuted,
            reasons=tuple(reasons),
        )
