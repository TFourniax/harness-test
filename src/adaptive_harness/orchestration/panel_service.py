from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from adaptive_harness.contracts import Goal, RunStatus
from adaptive_harness.orchestration.cell_runtime import CellSpec, LeafAttemptBudget, LeafOutcome
from adaptive_harness.orchestration.contracts import (
    VerificationCertificate,
    VerificationVerdict,
    WorkItem,
)
from adaptive_harness.orchestration.diversity_market import (
    IndependenceBreakdown,
    IndependenceScorer,
    MarginalDiversityMarket,
    PanelAttempt,
    evidence_first_select,
)
from adaptive_harness.orchestration.verification import VerificationEngine
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.v07_config import HarnessConfig


@dataclass
class _ObservedMarginal:
    slot_index: int
    attempt: PanelAttempt
    independence: IndependenceBreakdown
    before: VerificationCertificate
    after: VerificationCertificate


class IndependenceAwarePanelRunner:
    """Sequentially buy blind independent attempts only while their marginal value is justified.

    There is deliberately no peer transcript and no final LLM synthesis. Each real model call claims
    one shared LeafAttemptBudget slot immediately before execution. The deterministic selector chooses
    the strongest evidence-backed attempt; all concrete observations remain available to the combined
    certificate so contradictory postconditions can refute rather than be averaged away.
    """

    def __init__(
        self,
        *,
        config: HarnessConfig,
        child_runner: Callable,
        market: MarginalDiversityMarket,
        verifier: VerificationEngine | None = None,
        scorer: IndependenceScorer | None = None,
        max_panel_attempts: int = 3,
    ) -> None:
        self.config = config
        self.child_runner = child_runner
        self.market = market
        self.verifier = verifier or VerificationEngine()
        self.scorer = scorer or IndependenceScorer()
        self.max_panel_attempts = max(1, min(4, int(max_panel_attempts)))

    @staticmethod
    def _methods(profile: str | None) -> tuple[tuple[str, str], ...]:
        profile = (profile or "generic").lower()
        if profile == "code":
            return (
                (
                    "postcondition",
                    "Solve directly, then seek the strongest task-relevant deterministic postcondition. "
                    "Prefer tests/type/lint/build only when they genuinely cover this subtask.",
                ),
                (
                    "falsification",
                    "Independently try to falsify the likely solution. Focus on edge cases, regression "
                    "modes and a different verification path. Do not assume another worker is correct.",
                ),
                (
                    "invariants",
                    "Reason from invariants/contracts and inspect a different failure surface. Seek "
                    "concrete evidence that is not merely a repeat of the obvious happy-path check.",
                ),
            )
        if profile == "research":
            return (
                (
                    "primary-source",
                    "Build the answer from the strongest primary/current source path available.",
                ),
                (
                    "disconfirming-source",
                    "Independently search for disconfirming evidence, counterexamples or a source that "
                    "could falsify the leading interpretation. Do not inherit another worker's thesis.",
                ),
                (
                    "independent-source",
                    "Use a genuinely separate provenance path or source family. Prefer new evidence "
                    "over paraphrasing what another source would likely say.",
                ),
            )
        if profile == "scraping":
            return (
                ("schema", "Validate the extraction against schema and concrete samples."),
                (
                    "adversarial-sample",
                    "Use a different/adversarial page or sample shape and look for parser failure modes.",
                ),
                (
                    "independent-extraction",
                    "Independently reproduce key extracted facts through a different extraction path.",
                ),
            )
        if profile == "operations":
            return (
                ("direct-state", "Inspect current state and solve the bounded operational subtask."),
                (
                    "failure-mode",
                    "Independently test the failure/recovery path and check idempotence or rollback risks.",
                ),
                (
                    "postcondition",
                    "Verify the operational postcondition through a different observable state signal.",
                ),
            )
        return (
            ("direct-evidence", "Solve from concrete evidence and state uncertainty explicitly."),
            (
                "counterexample",
                "Use an independent approach centered on counterexamples or alternate assumptions. "
                "Do not assume another worker's conclusion.",
            ),
            (
                "alternate-evidence",
                "Seek a different evidence channel rather than a stylistic reformulation.",
            ),
        )

    def _role_for(self, spec: CellSpec, slot_index: int) -> str:
        if self.config.cheap is None:
            return "primary"
        # First attempt is efficient unless the task is intrinsically critical/hard. Later attempts
        # deliberately add model-role diversity only when the task justifies paying for it.
        threshold = 0.82
        if self.config.team is not None:
            threshold = self.config.team.economy.primary_preferred_difficulty
        if spec.critical or spec.difficulty >= threshold:
            return "primary" if slot_index in {1, 2} else "cheap"
        if slot_index == 3 and spec.difficulty >= 0.62:
            return "primary"
        return "cheap"

    def _model_id(self, role: str) -> str:
        if role == "cheap" and self.config.cheap is not None:
            return self.config.cheap.model
        return self.config.primary.model

    def _expected_cost(self, role: str) -> float:
        economy = self.config.team.economy if self.config.team is not None else None
        if economy is None:
            return 0.002 if role == "cheap" else 0.020
        return (
            economy.cold_start_cheap_call_usd
            if role == "cheap"
            else economy.cold_start_primary_call_usd
        )

    @staticmethod
    def _work_item(spec: CellSpec) -> WorkItem:
        task = spec.task
        if spec.success_criteria:
            task += "\nSUCCESS CRITERIA:\n- " + "\n- ".join(spec.success_criteria)
        return WorkItem(
            id=spec.id,
            task=task,
            profile=spec.profile,
            difficulty=spec.difficulty,
            critical=spec.critical,
        )

    async def _run_one(
        self,
        spec: CellSpec,
        *,
        slot_index: int,
        method_id: str,
        method_instruction: str,
        role: str,
        remaining_budget_usd: float,
    ) -> tuple[RunResult, PanelAttempt]:
        prompt = (
            "You are one BLIND independent attempt inside an adaptive panel. You cannot see peer "
            "answers and must not assume a consensus exists. Solve only this bounded subtask. Use "
            "allowed tools to create concrete, task-relevant evidence. Stylistic novelty has no value; "
            "new evidence or a genuinely different verification path does.\n\n"
            f"METHOD LANE: {method_id}\n{method_instruction}\n\n"
            f"SUBTASK:\n{spec.task}\n\n"
            "SUCCESS CRITERIA:\n- "
            + "\n- ".join(spec.success_criteria or ["Correctly solve and verify the assigned subtask"])
            + "\n\nCONSTRAINTS:\n- "
            + "\n- ".join(spec.constraints or ["Do not broaden scope"])
        )
        max_steps = self.config.team.worker_max_steps if self.config.team is not None else 18
        result: RunResult = await self.child_runner(
            Goal(
                text=prompt,
                profile=spec.profile,
                max_steps=max_steps,
                max_cost_usd=max(0.0, remaining_budget_usd),
                model_role=role,
            )
        )
        certificate = self.verifier.certify(self._work_item(spec), [result])
        answer = result.answer or f"attempt ended {result.status.value}"
        confidence = 0.84 if result.status == RunStatus.SUCCEEDED and role == "primary" else 0.74
        if result.status != RunStatus.SUCCEEDED:
            confidence = 0.06
        if certificate.verdict == VerificationVerdict.REFUTED:
            confidence = min(confidence, 0.10)
        elif certificate.verdict == VerificationVerdict.VERIFIED:
            confidence = max(confidence, 0.94)
        return result, PanelAttempt(
            method_id=method_id,
            model_role=role,
            model_id=self._model_id(role),
            answer=answer,
            cost_usd=max(0.0, float(result.reported_cost_usd)),
            confidence=confidence,
            evidence_refs=list(dict.fromkeys(obs.call_id for obs in result.observations if obs.ok)),
            verification=certificate,
        )

    async def __call__(
        self,
        spec: CellSpec,
        allowance_usd: float,
        attempt_budget: LeafAttemptBudget,
    ) -> LeafOutcome:
        methods = self._methods(spec.profile)
        attempts: list[PanelAttempt] = []
        run_results: list[RunResult] = []
        marginal_records: list[_ObservedMarginal] = []
        combined_before = VerificationCertificate()
        total_cost = 0.0

        for slot_index in range(1, min(self.max_panel_attempts, len(methods)) + 1):
            role = self._role_for(spec, slot_index)
            expected_cost = self._expected_cost(role)
            remaining = max(0.0, allowance_usd - total_cost)
            if slot_index > 1:
                decision = self.market.decide(
                    profile=spec.profile,
                    difficulty=spec.difficulty,
                    critical=spec.critical,
                    slot_index=slot_index,
                    current_certificate=combined_before,
                    expected_cost_usd=expected_cost,
                    remaining_budget_usd=remaining,
                )
                if not decision.buy:
                    break
            if not await attempt_budget.claim():
                break

            method_id, instruction = methods[slot_index - 1]
            result, panel_attempt = await self._run_one(
                spec,
                slot_index=slot_index,
                method_id=method_id,
                method_instruction=instruction,
                role=role,
                remaining_budget_usd=remaining,
            )
            total_cost += panel_attempt.cost_usd
            if total_cost > allowance_usd + 1e-12:
                # CellRuntime will enforce the hard escrow too; stop here so no further call is bought.
                attempts.append(panel_attempt)
                run_results.append(result)
                break

            prior_attempts = list(attempts)
            attempts.append(panel_attempt)
            run_results.append(result)
            combined_after = self.verifier.certify(self._work_item(spec), run_results)
            if slot_index > 1:
                independence = self.scorer.against_panel(panel_attempt, prior_attempts)
                marginal_records.append(
                    _ObservedMarginal(
                        slot_index=slot_index,
                        attempt=panel_attempt,
                        independence=independence,
                        before=combined_before,
                        after=combined_after,
                    )
                )
            combined_before = combined_after

        if not attempts:
            return LeafOutcome(
                answer="No panel attempt slot was available.",
                success=False,
                cost_usd=0.0,
                confidence=0.0,
                attempt_count=0,
            )

        selected = evidence_first_select(attempts)
        final_certificate = self.verifier.certify(self._work_item(spec), run_results)
        for record in marginal_records:
            self.market.record_outcome(
                profile=spec.profile,
                difficulty=spec.difficulty,
                critical=spec.critical,
                slot_index=record.slot_index,
                attempt=record.attempt,
                independence=record.independence,
                before=record.before,
                after=record.after,
                selected=record.attempt is selected,
            )

        # No majority vote: one task-relevant deterministic failure in the combined evidence can make
        # the whole panel non-successful even if another model confidently asserted the opposite.
        selected_success = selected.verification.verdict != VerificationVerdict.REFUTED
        combined_safe = final_certificate.verdict != VerificationVerdict.REFUTED
        success = bool(selected_success and combined_safe)
        confidence = selected.confidence
        if final_certificate.verdict == VerificationVerdict.VERIFIED:
            confidence = max(confidence, 0.95)
        elif final_certificate.evidence_strength < 0.20:
            confidence = min(confidence, 0.70)

        evidence_refs = list(
            dict.fromkeys(ref for item in attempts for ref in item.evidence_refs)
        )
        return LeafOutcome(
            answer=selected.answer,
            success=success,
            cost_usd=total_cost,
            confidence=confidence,
            evidence_refs=evidence_refs,
            attempt_count=len(attempts),
        )
