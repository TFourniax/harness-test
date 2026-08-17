from __future__ import annotations

from collections import Counter
from typing import Iterable

from adaptive_harness.contracts import RunStatus, TrustLevel
from adaptive_harness.orchestration.contracts import (
    VerificationCertificate,
    VerificationVerdict,
    WorkItem,
)
from adaptive_harness.runtime.agent import RunResult


_KIND_TERMS: dict[str, tuple[str, ...]] = {
    "tests": ("test", "pytest", "verify", "validate", "fix", "debug", "implement"),
    "lint": ("lint", "quality", "style", "static", "fix", "refactor"),
    "type_or_static_check": ("type", "static", "mypy", "pyright", "check", "validate"),
    "compile": ("compile", "syntax", "build", "implement", "fix"),
    "build": ("build", "bundle", "compile", "implement", "release"),
}


class VerificationEngine:
    """Compile trajectory evidence into a bounded, non-authorizing certificate.

    A passing command is *not* automatically proof of an arbitrary task. Deterministic checks are
    scored for scope relevance first. This prevents a worker from running an easy unrelated command
    and laundering it into a high-confidence certificate for a broader claim.
    """

    @staticmethod
    def _deterministic_coverage(task: WorkItem, kind: str, claim: str) -> float:
        task_text = task.task.lower()
        terms = _KIND_TERMS.get(kind, ())
        # A check description naturally names its own mechanism. Relevance must come
        # from the assigned task (or a narrow profile prior), not from that self-description.
        task_explicit = any(term in task_text for term in terms)
        profile = (task.profile or "generic").lower()
        if task_explicit:
            if kind == "tests":
                return 0.94
            if kind in {"type_or_static_check", "lint"}:
                return 0.82
            return 0.78
        if profile == "code":
            if kind == "tests":
                return 0.78
            if kind in {"type_or_static_check", "lint"}:
                return 0.62
            return 0.52
        if profile in {"scraping", "operations"}:
            return 0.38
        return 0.22

    def certify(self, task: WorkItem, results: Iterable[RunResult]) -> VerificationCertificate:
        results = list(results)
        observations = [obs for result in results for obs in result.observations]
        evidence_refs = [obs.call_id for obs in observations if obs.ok]

        deterministic: list[tuple[object, bool, float, str]] = []
        source_hosts: set[str] = set()
        trusted_success = []
        external_success = []
        checks: list[str] = []
        reasons: list[str] = []

        for obs in observations:
            signal = str(obs.metadata.get("verification_signal", ""))
            claim = str(obs.metadata.get("verification_claim", "")).strip()
            kind = str(obs.metadata.get("verification_kind", "")).strip()
            label = ": ".join(part for part in (kind, claim) if part)

            if signal in {"deterministic_pass", "deterministic_fail"} and obs.ok:
                coverage = self._deterministic_coverage(task, kind, claim)
                deterministic.append((obs, signal == "deterministic_pass", coverage, label))
                checks.append(label or "deterministic postcondition")

            host = str(obs.metadata.get("source_host", "")).lower().strip()
            if host and obs.ok:
                source_hosts.add(host)
            if obs.ok and obs.trust == TrustLevel.UNTRUSTED_EXTERNAL:
                external_success.append(obs)
            elif obs.ok and obs.trust in {TrustLevel.TOOL, TrustLevel.TRUSTED}:
                trusted_success.append(obs)

        # A failing deterministic check may refute the bounded task only when the check has material
        # coverage. Otherwise it remains negative evidence, not a universal refutation.
        failed = [item for item in deterministic if not item[1]]
        if failed:
            coverage = max(item[2] for item in failed)
            if coverage >= 0.60:
                reasons.append(
                    f"a task-relevant deterministic success postcondition failed (coverage={coverage:.2f})"
                )
                return VerificationCertificate(
                    verdict=VerificationVerdict.REFUTED,
                    evidence_strength=min(1.0, 0.70 + 0.30 * coverage),
                    score=0.0,
                    deterministic=True,
                    scope_coverage=coverage,
                    independent_sources=len(source_hosts),
                    checks=checks,
                    evidence_refs=evidence_refs,
                    reasons=reasons,
                )
            reasons.append(
                f"a deterministic check failed but has low scope coverage ({coverage:.2f})"
            )

        passed = [item for item in deterministic if item[1]]
        if passed:
            coverage = max(item[2] for item in passed)
            kind = str(passed[0][0].metadata.get("verification_kind", ""))
            # Only a strongly task-bound postcondition earns VERIFIED. Generic compile/lint/build
            # success is supporting evidence, not proof of the whole answer.
            if coverage >= 0.88:
                reasons.append(
                    f"a strongly task-bound deterministic postcondition passed (coverage={coverage:.2f})"
                )
                return VerificationCertificate(
                    verdict=VerificationVerdict.VERIFIED,
                    evidence_strength=min(0.98, 0.82 + 0.16 * coverage),
                    score=min(0.99, 0.88 + 0.10 * coverage),
                    deterministic=True,
                    scope_coverage=coverage,
                    independent_sources=len(source_hosts),
                    checks=checks,
                    evidence_refs=evidence_refs,
                    reasons=reasons,
                )
            strength_base = 0.38
            if kind == "tests":
                strength_base = 0.52
            elif kind in {"type_or_static_check", "lint"}:
                strength_base = 0.44
            strength = min(0.82, strength_base + 0.32 * coverage)
            reasons.append(
                f"deterministic check passed but verifies only part of the task (coverage={coverage:.2f})"
            )
            return VerificationCertificate(
                verdict=VerificationVerdict.SUPPORTED,
                evidence_strength=strength,
                score=min(0.86, 0.52 + 0.34 * coverage),
                deterministic=True,
                scope_coverage=coverage,
                independent_sources=len(source_hosts),
                checks=checks,
                evidence_refs=evidence_refs,
                reasons=reasons,
            )

        if len(source_hosts) >= 3:
            reasons.append(f"evidence retrieved from {len(source_hosts)} distinct public hosts")
            return VerificationCertificate(
                verdict=VerificationVerdict.SUPPORTED,
                evidence_strength=0.72,
                score=0.76,
                deterministic=False,
                scope_coverage=0.62,
                independent_sources=len(source_hosts),
                checks=checks,
                evidence_refs=evidence_refs,
                reasons=reasons,
            )
        if len(source_hosts) >= 2:
            reasons.append("evidence retrieved from two distinct public hosts")
            return VerificationCertificate(
                verdict=VerificationVerdict.SUPPORTED,
                evidence_strength=0.60,
                score=0.68,
                deterministic=False,
                scope_coverage=0.52,
                independent_sources=len(source_hosts),
                checks=checks,
                evidence_refs=evidence_refs,
                reasons=reasons,
            )

        succeeded = [result for result in results if result.status == RunStatus.SUCCEEDED]
        if len(trusted_success) >= 2:
            reasons.append("multiple successful local/tool observations support the work product")
            return VerificationCertificate(
                verdict=VerificationVerdict.SUPPORTED,
                evidence_strength=0.42,
                score=0.58,
                deterministic=False,
                scope_coverage=0.38,
                independent_sources=len(source_hosts),
                checks=checks,
                evidence_refs=evidence_refs,
                reasons=reasons,
            )
        if trusted_success or external_success:
            reasons.append("at least one concrete observation exists, but no strong postcondition")
            return VerificationCertificate(
                verdict=VerificationVerdict.SUPPORTED,
                evidence_strength=0.28,
                score=0.48,
                deterministic=False,
                scope_coverage=0.25,
                independent_sources=len(source_hosts),
                checks=checks,
                evidence_refs=evidence_refs,
                reasons=reasons,
            )

        if len(succeeded) >= 2:
            answers = [str(result.answer or "").strip().lower()[:1200] for result in succeeded]
            distinct = len(Counter(answers))
            reasons.append(
                "multiple model attempts succeeded but produced no environment-backed evidence"
            )
            return VerificationCertificate(
                verdict=VerificationVerdict.UNVERIFIED,
                evidence_strength=0.14 if distinct > 1 else 0.18,
                score=0.50,
                deterministic=False,
                scope_coverage=0.0,
                independent_sources=0,
                evidence_refs=[],
                reasons=reasons,
            )

        if succeeded:
            reasons.append("model run succeeded without a concrete verification observation")
            return VerificationCertificate(
                verdict=VerificationVerdict.UNVERIFIED,
                evidence_strength=0.08,
                score=0.50,
                deterministic=False,
                scope_coverage=0.0,
                evidence_refs=[],
                reasons=reasons,
            )

        reasons.append("no successful worker result or verification evidence")
        return VerificationCertificate(
            verdict=VerificationVerdict.UNVERIFIED,
            evidence_strength=0.02,
            score=0.0,
            deterministic=False,
            scope_coverage=0.0,
            evidence_refs=evidence_refs,
            reasons=reasons,
        )
