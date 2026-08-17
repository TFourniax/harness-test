from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from adaptive_harness.orchestration.contracts import VerificationCertificate, VerificationVerdict
from adaptive_harness.orchestration.vector_cache import HashingVectorizer


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


class PanelAttempt(BaseModel):
    method_id: str
    model_role: str
    model_id: str = ""
    answer: str
    succeeded: bool = True
    cost_usd: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Exact call IDs remain the audit trail. Canonical channels collapse repeated invocations of the
    # same tool+arguments so two agents cannot manufacture independence by rereading the same source.
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_channels: list[str] = Field(default_factory=list)
    verification: VerificationCertificate = Field(default_factory=VerificationCertificate)

    def independence_evidence(self) -> set[str]:
        values = self.evidence_channels or self.evidence_refs
        return {str(value) for value in values if str(value)}


class IndependenceBreakdown(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    evidence_novelty: float = Field(ge=0.0, le=1.0)
    answer_novelty: float = Field(ge=0.0, le=1.0)
    method_novelty: float = Field(ge=0.0, le=1.0)
    model_novelty: float = Field(ge=0.0, le=1.0)


class MarginalDecision(BaseModel):
    buy: bool
    slot_index: int = Field(ge=2)
    expected_gain: float = Field(ge=0.0, le=1.0)
    expected_independence: float = Field(ge=0.0, le=1.0)
    expected_cost_usd: float = Field(ge=0.0)
    utility: float
    sample_count: int = 0
    reason: str


class IndependenceScorer:
    """Estimate whether one attempt opens a genuinely different information channel.

    Textual disagreement is intentionally a minority signal. Two fluent answers with no concrete
    evidence do not become trustworthy merely because their wording differs. Canonical evidence
    channels dominate the score, followed by answer semantics, deliberate method diversity and model
    diversity. Exact call IDs are used only as a backward-compatible fallback when a runtime cannot
    yet provide canonical provenance.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.vectorizer = HashingVectorizer(dimensions)

    @staticmethod
    def _evidence_novelty(candidate: set[str], prior: set[str]) -> float:
        if not candidate and not prior:
            return 0.0
        if not candidate:
            return 0.0
        if not prior:
            return 1.0
        union = candidate | prior
        intersection = candidate & prior
        return _clamp(1.0 - len(intersection) / max(1, len(union)))

    def pair(self, candidate: PanelAttempt, prior: PanelAttempt) -> IndependenceBreakdown:
        candidate_evidence = candidate.independence_evidence()
        prior_evidence = prior.independence_evidence()
        evidence = self._evidence_novelty(candidate_evidence, prior_evidence)
        a = self.vectorizer.encode(candidate.answer)
        b = self.vectorizer.encode(prior.answer)
        cosine = self.vectorizer.cosine(a, b)
        answer = _clamp(1.0 - max(0.0, cosine))
        method = 1.0 if candidate.method_id != prior.method_id else 0.0
        model = 1.0 if (
            candidate.model_role != prior.model_role
            or (candidate.model_id and prior.model_id and candidate.model_id != prior.model_id)
        ) else 0.0
        score = _clamp(0.55 * evidence + 0.20 * answer + 0.15 * method + 0.10 * model)
        return IndependenceBreakdown(
            score=score,
            evidence_novelty=evidence,
            answer_novelty=answer,
            method_novelty=method,
            model_novelty=model,
        )

    def against_panel(
        self, candidate: PanelAttempt, prior_attempts: list[PanelAttempt]
    ) -> IndependenceBreakdown:
        if not prior_attempts:
            return IndependenceBreakdown(
                score=1.0,
                evidence_novelty=1.0 if candidate.independence_evidence() else 0.0,
                answer_novelty=1.0,
                method_novelty=1.0,
                model_novelty=1.0,
            )
        pairs = [self.pair(candidate, prior) for prior in prior_attempts]
        return IndependenceBreakdown(
            score=min(item.score for item in pairs),
            evidence_novelty=min(item.evidence_novelty for item in pairs),
            answer_novelty=min(item.answer_novelty for item in pairs),
            method_novelty=min(item.method_novelty for item in pairs),
            model_novelty=min(item.model_novelty for item in pairs),
        )


@dataclass(frozen=True)
class MarginalStats:
    samples: int
    independence: float
    evidence_gain: float
    score_gain: float
    coverage_gain: float
    cost_usd: float
    selected_rate: float

    @property
    def verification_gain(self) -> float:
        return _clamp(0.55 * self.evidence_gain + 0.25 * self.score_gain + 0.20 * self.coverage_gain)


class MarginalDiversityStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS marginal_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    method_id TEXT NOT NULL,
                    independence REAL NOT NULL,
                    marginal_evidence_gain REAL NOT NULL,
                    marginal_score_gain REAL NOT NULL,
                    marginal_coverage_gain REAL NOT NULL,
                    cost_usd REAL NOT NULL,
                    selected INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_marginal_bucket_slot "
                "ON marginal_attempts(bucket, slot_index, id)"
            )

    def record(
        self,
        *,
        bucket: str,
        slot_index: int,
        method_id: str,
        independence: float,
        marginal_evidence_gain: float,
        marginal_score_gain: float,
        marginal_coverage_gain: float,
        cost_usd: float,
        selected: bool,
    ) -> None:
        if slot_index < 2:
            raise ValueError("marginal observations start at the second attempt")
        with sqlite3.connect(self.path) as db:
            db.execute(
                """
                INSERT INTO marginal_attempts(
                    bucket,slot_index,method_id,independence,marginal_evidence_gain,
                    marginal_score_gain,marginal_coverage_gain,cost_usd,selected,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    bucket,
                    int(slot_index),
                    method_id,
                    _clamp(independence),
                    _clamp(marginal_evidence_gain),
                    _clamp(marginal_score_gain),
                    _clamp(marginal_coverage_gain),
                    max(0.0, float(cost_usd)),
                    int(bool(selected)),
                    _utcnow(),
                ),
            )

    def stats(self, bucket: str, slot_index: int, *, limit: int = 80) -> MarginalStats | None:
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                """
                SELECT independence,marginal_evidence_gain,marginal_score_gain,
                       marginal_coverage_gain,cost_usd,selected
                FROM marginal_attempts
                WHERE bucket=? AND slot_index=?
                ORDER BY id DESC LIMIT ?
                """,
                (bucket, int(slot_index), max(1, int(limit))),
            ).fetchall()
        if not rows:
            return None
        n = len(rows)
        return MarginalStats(
            samples=n,
            independence=sum(float(row[0]) for row in rows) / n,
            evidence_gain=sum(float(row[1]) for row in rows) / n,
            score_gain=sum(float(row[2]) for row in rows) / n,
            coverage_gain=sum(float(row[3]) for row in rows) / n,
            cost_usd=sum(float(row[4]) for row in rows) / n,
            selected_rate=sum(int(row[5]) for row in rows) / n,
        )


class MarginalDiversityMarket:
    def __init__(
        self,
        store: MarginalDiversityStore,
        *,
        min_samples: int = 5,
        min_utility: float = 0.025,
        cost_weight: float = 3.0,
        redundancy_floor: float = 0.24,
    ) -> None:
        self.store = store
        self.min_samples = max(1, int(min_samples))
        self.min_utility = max(0.0, float(min_utility))
        self.cost_weight = max(0.0, float(cost_weight))
        self.redundancy_floor = _clamp(redundancy_floor)

    @staticmethod
    def bucket(profile: str | None, difficulty: float, critical: bool) -> str:
        band = "easy" if difficulty < 0.45 else "medium" if difficulty < 0.75 else "hard"
        return f"{profile or 'generic'}:{band}:{'critical' if critical else 'normal'}"

    @staticmethod
    def _prior(slot_index: int, difficulty: float, critical: bool) -> tuple[float, float]:
        base_gain = 0.11 + 0.13 * _clamp(difficulty) + (0.08 if critical else 0.0)
        base_independence = 0.48 + 0.12 * _clamp(difficulty)
        if slot_index >= 3:
            base_gain *= 0.58
            base_independence *= 0.82
        return _clamp(base_gain), _clamp(base_independence)

    @staticmethod
    def _strong_stop(certificate: VerificationCertificate) -> bool:
        return bool(
            certificate.verdict == VerificationVerdict.VERIFIED
            and certificate.evidence_strength >= 0.90
            and certificate.scope_coverage >= 0.85
        )

    def decide(
        self,
        *,
        profile: str | None,
        difficulty: float,
        critical: bool,
        slot_index: int,
        current_certificate: VerificationCertificate,
        expected_cost_usd: float,
        remaining_budget_usd: float,
    ) -> MarginalDecision:
        if slot_index < 2:
            raise ValueError("slot_index must be >= 2")
        expected_cost = max(0.0, float(expected_cost_usd))
        remaining = max(0.0, float(remaining_budget_usd))
        if expected_cost > remaining + 1e-12:
            return MarginalDecision(
                buy=False,
                slot_index=slot_index,
                expected_gain=0.0,
                expected_independence=0.0,
                expected_cost_usd=expected_cost,
                utility=-self.cost_weight * expected_cost,
                reason="remaining escrow cannot fund the next real attempt",
            )
        if self._strong_stop(current_certificate):
            return MarginalDecision(
                buy=False,
                slot_index=slot_index,
                expected_gain=0.0,
                expected_independence=0.0,
                expected_cost_usd=expected_cost,
                utility=-self.cost_weight * expected_cost,
                reason="current result already has strong task-bound verified evidence",
            )
        bucket = self.bucket(profile, difficulty, critical)
        stats = self.store.stats(bucket, slot_index)
        prior_gain, prior_independence = self._prior(slot_index, difficulty, critical)
        samples = stats.samples if stats else 0
        if stats and samples >= self.min_samples:
            expected_gain = stats.verification_gain
            expected_independence = stats.independence
            empirical_cost = stats.cost_usd if stats.cost_usd > 0 else expected_cost
            expected_cost = max(expected_cost, empirical_cost)
        elif stats:
            weight = samples / self.min_samples
            expected_gain = (1 - weight) * prior_gain + weight * stats.verification_gain
            expected_independence = (
                (1 - weight) * prior_independence + weight * stats.independence
            )
        else:
            expected_gain = prior_gain
            expected_independence = prior_independence
        useful_gain = expected_gain * (0.45 + 0.55 * expected_independence)
        utility = useful_gain - self.cost_weight * expected_cost
        if stats and samples >= self.min_samples and expected_independence < self.redundancy_floor:
            buy = False
            reason = (
                f"historical attempt {slot_index} is redundant for {bucket}: "
                f"independence={expected_independence:.3f}"
            )
        else:
            buy = utility >= self.min_utility
            reason = (
                f"marginal slot {slot_index} for {bucket}: gain={expected_gain:.3f} "
                f"independence={expected_independence:.3f} cost=${expected_cost:.6f} "
                f"utility={utility:.3f}"
            )
        return MarginalDecision(
            buy=buy,
            slot_index=slot_index,
            expected_gain=_clamp(expected_gain),
            expected_independence=_clamp(expected_independence),
            expected_cost_usd=expected_cost,
            utility=utility,
            sample_count=samples,
            reason=reason,
        )

    @staticmethod
    def marginal_gains(
        before: VerificationCertificate,
        after: VerificationCertificate,
    ) -> tuple[float, float, float]:
        return (
            max(0.0, after.evidence_strength - before.evidence_strength),
            max(0.0, after.score - before.score),
            max(0.0, after.scope_coverage - before.scope_coverage),
        )

    def record_outcome(
        self,
        *,
        profile: str | None,
        difficulty: float,
        critical: bool,
        slot_index: int,
        attempt: PanelAttempt,
        independence: IndependenceBreakdown,
        before: VerificationCertificate,
        after: VerificationCertificate,
        selected: bool,
    ) -> None:
        evidence_gain, score_gain, coverage_gain = self.marginal_gains(before, after)
        self.store.record(
            bucket=self.bucket(profile, difficulty, critical),
            slot_index=slot_index,
            method_id=attempt.method_id,
            independence=independence.score,
            marginal_evidence_gain=evidence_gain,
            marginal_score_gain=score_gain,
            marginal_coverage_gain=coverage_gain,
            cost_usd=attempt.cost_usd,
            selected=selected,
        )


def certificate_rank(certificate: VerificationCertificate) -> tuple[float, ...]:
    verdict_rank = {
        VerificationVerdict.REFUTED: 0.0,
        VerificationVerdict.UNVERIFIED: 1.0,
        VerificationVerdict.SUPPORTED: 2.0,
        VerificationVerdict.VERIFIED: 3.0,
    }[certificate.verdict]
    return (
        verdict_rank,
        certificate.evidence_strength,
        certificate.scope_coverage,
        1.0 if certificate.deterministic else 0.0,
        float(certificate.independent_sources),
        certificate.score,
    )


def evidence_first_select(attempts: list[PanelAttempt]) -> PanelAttempt:
    if not attempts:
        raise ValueError("cannot select from an empty panel")
    return max(
        attempts,
        key=lambda attempt: (
            *certificate_rank(attempt.verification),
            1.0 if attempt.succeeded else 0.0,
            attempt.confidence,
            -attempt.cost_usd,
        ),
    )
