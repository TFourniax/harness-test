from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from adaptive_harness.config import HarnessConfig
from adaptive_harness.contracts import Goal, RunStatus, ToolExecutionResult, TraceEvent, TrustLevel
from adaptive_harness.orchestration.compute_market import (
    ComputeAction,
    ComputeBid,
    ComputeEconomyStore,
    ComputeMarket,
)
from adaptive_harness.orchestration.confidence import (
    ConfidenceCalibrationStore,
    TrajectoryConfidenceCalibrator,
)
from adaptive_harness.orchestration.contracts import (
    AgentReport,
    ContextCapsule,
    FollowUp,
    Freshness,
    SynthesisDecision,
    VerificationVerdict,
    WorkItem,
)
from adaptive_harness.orchestration.policy_arena import ComputePolicy, PolicyArenaStore
from adaptive_harness.orchestration.team import TeamOrchestrator
from adaptive_harness.orchestration.verification import VerificationEngine


@dataclass
class StrategyRecord:
    task: WorkItem
    bid: ComputeBid
    report: AgentReport
    operational_success: bool
    matched_shadow_policies: tuple[str, ...] = ()


class AdaptiveTeamOrchestrator(TeamOrchestrator):
    """v0.5 sparse team loop with evidence-grounded adaptive compute."""

    def __init__(self, *, config: HarnessConfig, provider, traces, child_runner, cache=None) -> None:
        super().__init__(
            config=config,
            provider=provider,
            traces=traces,
            child_runner=child_runner,
            cache=cache,
        )
        economy_path = Path(config.team.economy.db)
        if not economy_path.is_absolute():
            economy_path = Path(config.harness_root) / economy_path
        self.market_store = ComputeEconomyStore(
            economy_path,
            evidence_half_life_days=config.team.economy.evidence_half_life_days,
        )
        self.market = ComputeMarket(config, self.market_store)

        confidence_path = Path(config.team.economy.confidence_db)
        if not confidence_path.is_absolute():
            confidence_path = Path(config.harness_root) / confidence_path
        self.confidence_store = ConfidenceCalibrationStore(confidence_path)
        self.calibrator = TrajectoryConfidenceCalibrator(
            self.confidence_store,
            min_empirical_samples=config.team.economy.confidence_min_empirical_samples,
        )
        self.verification = VerificationEngine()

        self.policy_arena: PolicyArenaStore | None = None
        if config.team.economy.policy_arena_enabled:
            policy_path = Path(config.team.economy.policy_arena_db)
            if not policy_path.is_absolute():
                policy_path = Path(config.harness_root) / policy_path
            self.policy_arena = PolicyArenaStore(
                policy_path,
                min_matched_trials=config.team.economy.policy_min_matched_trials,
                quality_regression_tolerance=(
                    config.team.economy.policy_quality_regression_tolerance
                ),
                quality_gain_target=config.team.economy.policy_quality_gain_target,
                cost_reduction_target=config.team.economy.policy_cost_reduction_target,
                cost_tolerance=config.team.economy.policy_cost_tolerance,
                evidence_floor=config.team.economy.policy_evidence_floor,
            )

        self._active_policy = ComputePolicy("balanced-v1", "balanced", {}, "champion")
        self._challengers: list[ComputePolicy] = []
        self._active_budget_usd: float | None = None
        self._spent_usd = 0.0
        self._current_confidence = 0.0
        self._raw_confidence = 0.0
        self._evidence_strength = 0.0
        self._child_attempts = 0
        self._child_attempt_cap = 1
        self._attempt_lock = asyncio.Lock()
        self._strategy_records: list[StrategyRecord] = []
        self._decision_log: list[str] = []

    def _budget_remaining(self) -> float | None:
        if self._active_budget_usd is None:
            return None
        return max(0.0, self._active_budget_usd - self._spent_usd)

    async def _reserve_attempts(
        self, task: WorkItem, bid: ComputeBid
    ) -> tuple[ComputeBid, int]:
        requested = 2 if bid.action in {ComputeAction.CHEAP_PAIR, ComputeAction.MIXED_PAIR} else 1
        async with self._attempt_lock:
            remaining = self._child_attempt_cap - self._child_attempts
            if remaining <= 0:
                stop = ComputeBid(
                    action=ComputeAction.STOP,
                    expected_success=self._current_confidence,
                    expected_gain=0.0,
                    estimated_cost_usd=0.0,
                    utility=0.0,
                    reason="child-attempt cap exhausted",
                    policy_id=self._active_policy.id,
                )
                return stop, 0
            if requested > remaining:
                fallback_action = (
                    ComputeAction.PRIMARY_SINGLE
                    if bid.action == ComputeAction.MIXED_PAIR
                    else ComputeAction.CHEAP_SINGLE
                )
                budget_left = self._budget_remaining()
                bid = self.market.quote(
                    fallback_action,
                    task,
                    current_confidence=self._current_confidence,
                    remaining_budget_usd=budget_left,
                    policy=self._active_policy.params,
                    policy_id=self._active_policy.id,
                )
                if budget_left is not None and bid.estimated_cost_usd > budget_left:
                    stop = ComputeBid(
                        action=ComputeAction.STOP,
                        expected_success=self._current_confidence,
                        expected_gain=0.0,
                        estimated_cost_usd=0.0,
                        utility=0.0,
                        reason="hard remaining budget cannot fund slot-constrained fallback",
                        policy_id=self._active_policy.id,
                    )
                    return stop, 0
                requested = 1
            self._child_attempts += requested
            return bid, requested

    def _steps_for(self, task: WorkItem, role: str) -> int:
        if role == "primary":
            return self.team.worker_max_steps
        fraction = 0.50 + 0.35 * task.difficulty
        return max(5, min(self.team.worker_max_steps, int(self.team.worker_max_steps * fraction)))

    async def _run_child(
        self,
        *,
        capsule: ContextCapsule,
        task: WorkItem,
        role: str,
        variant: str,
    ):
        suffix = ""
        if variant:
            suffix = (
                "\n\nINDEPENDENCE INSTRUCTION:\n"
                + variant
                + "\nDo not assume another worker's conclusion."
            )
        return await self.child_runner(
            Goal(
                text=capsule.render() + suffix,
                profile=task.profile,
                max_steps=self._steps_for(task, role),
                model_role=role,
            )
        )

    @staticmethod
    def _worker_confidence(raw: float, certificate) -> float:
        value = max(0.0, min(1.0, raw))
        if certificate.verdict == VerificationVerdict.REFUTED:
            return min(value, 0.18)
        if certificate.verdict == VerificationVerdict.VERIFIED and certificate.deterministic:
            return max(value, 0.94)
        if certificate.evidence_strength < 0.20:
            return min(value, 0.76)
        return min(0.96, value * (0.82 + 0.18 * certificate.evidence_strength))

    def _task_key(self, task: WorkItem) -> str:
        payload = "|".join(
            [
                task.profile or "generic",
                f"{task.difficulty:.2f}",
                str(int(task.critical)),
                task.task,
                self.context_fingerprint,
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _shadow_policy_matches(self, task: WorkItem, actual: ComputeBid) -> tuple[str, ...]:
        matches: list[str] = []
        for policy in self._challengers:
            shadow = self.market.choose(
                task,
                current_confidence=self._current_confidence,
                remaining_budget_usd=self._budget_remaining(),
                policy=policy.params,
                policy_id=policy.id,
                record_decision=False,
            )
            self._decision_log.append(
                f"{task.id}: shadow[{policy.id}]={shadow.action.value} utility={shadow.utility:.3f}"
            )
            if shadow.action == actual.action:
                matches.append(policy.id)
        return tuple(matches)

    async def _run_item(
        self,
        task: WorkItem,
        root_goal: str,
        success_criteria: list[str],
        constraints: list[str],
        reports: dict[str, AgentReport],
    ) -> AgentReport:
        deps = [reports[dep].compact() for dep in task.dependencies if dep in reports]
        semantic_text = task.task + "\n" + "\n".join(deps)
        lookup = None
        if self.cache is not None and task.cacheable:
            lookup = self.cache.lookup(
                namespace="team-work",
                kind=task.profile or "generic",
                semantic_text=semantic_text,
                context_fingerprint=self.context_fingerprint,
                direct_threshold=self.team.semantic_direct_threshold,
                reference_threshold=self.team.semantic_reference_threshold,
                allow_semantic_direct=task.freshness != Freshness.VOLATILE,
            )
            if lookup.status == "exact" and lookup.value:
                report = AgentReport.model_validate(lookup.value)
                # v0.5 rejects legacy direct entries that do not carry enough verification evidence.
                certificate = report.verification
                min_strength = self.team.economy.direct_cache_min_evidence_strength
                if certificate is not None and certificate.evidence_strength >= min_strength:
                    report.status = "cached"
                    report.cache_status = "exact"
                    report.cost_usd = 0.0
                    self._decision_log.append(
                        f"{task.id}: cache_direct similarity={lookup.similarity:.3f} "
                        f"evidence={certificate.evidence_strength:.2f}"
                    )
                    return report
                # Downgrade an old/excessively weak direct result to a hint.
                lookup = lookup.__class__(
                    "semantic_reference", lookup.value, lookup.similarity, lookup.key
                )

        reference_hint = None
        if lookup and lookup.status == "semantic_reference" and lookup.value:
            reference_hint = str(lookup.value.get("answer", ""))

        capsule = ContextCapsule(
            root_goal=root_goal,
            subtask=task.task,
            success_criteria=success_criteria,
            constraints=[
                *constraints,
                (
                    "You are a specialist worker. Do not execute externally visible side effects; "
                    "return analysis/evidence to the parent."
                ),
            ],
            dependency_summaries=deps,
            reference_hint=reference_hint,
        )

        bid = self.market.choose(
            task,
            current_confidence=self._current_confidence,
            remaining_budget_usd=self._budget_remaining(),
            policy=self._active_policy.params,
            policy_id=self._active_policy.id,
            record_decision=True,
        )
        bid, reserved = await self._reserve_attempts(task, bid)
        matched_shadow = self._shadow_policy_matches(task, bid)
        self._decision_log.append(f"{task.id}: {bid.action.value} {bid.reason}")
        if bid.action == ComputeAction.STOP or reserved == 0:
            return AgentReport(
                task_id=task.id,
                answer=f"Skipped by adaptive compute market: {bid.reason}",
                confidence=self._current_confidence,
                role="cheap",
                status="skipped",
                cache_status="semantic_reference" if reference_hint else "miss",
                attempt_count=0,
            )

        if bid.action == ComputeAction.CHEAP_SINGLE:
            roles = ["cheap"]
            variants = [""]
        elif bid.action == ComputeAction.CHEAP_PAIR:
            roles = ["cheap", "cheap"]
            variants = [
                "Use the most direct evidence-first approach.",
                "Use a deliberately different approach and look for counterexamples.",
            ]
        elif bid.action == ComputeAction.PRIMARY_SINGLE:
            roles = ["primary"]
            variants = [""]
        else:
            roles = ["cheap", "primary"]
            variants = [
                "Produce an efficient first-pass solution and flag uncertainty.",
                "Independently verify the problem from first principles; focus on failure modes.",
            ]

        child_results = await asyncio.gather(
            *(
                self._run_child(
                    capsule=capsule,
                    task=task,
                    role=role,
                    variant=variant,
                )
                for role, variant in zip(roles, variants)
            ),
            return_exceptions=True,
        )

        valid_results = [r for r in child_results if not isinstance(r, Exception)]
        succeeded_results = [r for r in valid_results if r.status == RunStatus.SUCCEEDED]
        total_cost = sum(float(r.reported_cost_usd) for r in valid_results)
        answers: list[str] = []
        evidence_refs: list[str] = []
        for index, (role, result) in enumerate(zip(roles, child_results), start=1):
            if isinstance(result, Exception):
                answers.append(
                    f"FAILED ATTEMPT {index} ({role}): {type(result).__name__}: {result}"
                )
                continue
            answers.append(
                f"INDEPENDENT ATTEMPT {index} ({role}):\n"
                + (result.answer or f"status={result.status.value}")
            )
            evidence_refs.extend(obs.call_id for obs in result.observations if obs.ok)

        succeeded = bool(succeeded_results)
        if not succeeded:
            raw_worker_confidence = 0.05
        elif len(roles) == 1:
            raw_worker_confidence = 0.80 if roles[0] == "primary" else 0.72
        elif len(succeeded_results) == len(roles):
            raw_worker_confidence = 0.84 if bid.action == ComputeAction.CHEAP_PAIR else 0.88
        else:
            raw_worker_confidence = 0.64

        joined = "\n\n".join(answers)
        if any(
            token in joined.lower()
            for token in ("uncertain", "unknown", "cannot verify", "not sure")
        ):
            raw_worker_confidence = max(0.05, raw_worker_confidence - 0.12)

        certificate = self.verification.certify(task, valid_results)
        confidence = self._worker_confidence(raw_worker_confidence, certificate)
        report = AgentReport(
            task_id=task.id,
            answer=joined or "No worker result.",
            confidence=confidence,
            role="primary" if "primary" in roles else "cheap",
            status="succeeded" if succeeded else "failed",
            cost_usd=total_cost,
            cache_status="semantic_reference" if reference_hint else "miss",
            evidence_refs=evidence_refs,
            verification=certificate,
            attempt_count=len(roles),
        )
        self._strategy_records.append(
            StrategyRecord(task, bid, report, succeeded, matched_shadow)
        )

        if self.cache is not None and task.cacheable and succeeded:
            all_direct_safe = (
                len(succeeded_results) == len(roles)
                and all(self._direct_cache_safe(task, result) for result in succeeded_results)
                and certificate.evidence_strength
                >= self.team.economy.direct_cache_min_evidence_strength
                and certificate.verdict
                in {VerificationVerdict.VERIFIED, VerificationVerdict.SUPPORTED}
            )
            ttl = (
                self.team.volatile_cache_ttl_seconds
                if task.freshness == Freshness.VOLATILE
                else self.team.stable_cache_ttl_seconds
            )
            report.cache_key = self.cache.put(
                namespace="team-work",
                kind=task.profile or "generic",
                semantic_text=semantic_text,
                context_fingerprint=self.context_fingerprint,
                value=report.model_dump(mode="json"),
                verified=all_direct_safe,
                allow_direct=all_direct_safe,
                ttl_seconds=ttl,
            )
        return report

    def _record_strategy_learning(self, records: list[StrategyRecord], quality_gain: float) -> None:
        if not records:
            return
        weight_total = sum(max(0.05, record.report.confidence) for record in records)
        for record in records:
            report = record.report
            certificate = report.verification
            evidence_strength = certificate.evidence_strength if certificate else 0.05
            outcome_score = certificate.learning_success if certificate else 0.5
            share = quality_gain * max(0.05, report.confidence) / weight_total
            self.market.record(
                record.bid,
                record.task,
                succeeded=record.operational_success,
                cost_usd=report.cost_usd,
                quality_gain=share,
                evidence_strength=evidence_strength,
                outcome_score=outcome_score,
            )
            if self.policy_arena is not None:
                task_key = self._task_key(record.task)
                passed = (
                    record.operational_success
                    and certificate is not None
                    and certificate.verdict != VerificationVerdict.REFUTED
                )
                self.policy_arena.record_trial(
                    policy_id=self._active_policy.id,
                    task_key=task_key,
                    quality=report.confidence,
                    cost_usd=report.cost_usd,
                    evidence_strength=evidence_strength,
                    passed=passed,
                    source="live",
                )
                # When a challenger would have chosen the exact same action, the observed outcome is
                # a valid zero-extra-cost matched-action observation. It is diagnostic only and is
                # intentionally excluded from automatic promotion evidence.
                for policy_id in record.matched_shadow_policies:
                    self.policy_arena.record_trial(
                        policy_id=policy_id,
                        task_key=task_key,
                        quality=report.confidence,
                        cost_usd=report.cost_usd,
                        evidence_strength=evidence_strength,
                        passed=passed,
                        source="matched_action",
                    )

    async def execute(
        self,
        *,
        root_goal: str,
        success_criteria: list[str],
        constraints: list[str],
        max_agents: int | None,
        max_cost_usd: float | None,
    ) -> ToolExecutionResult:
        if self.team is None or not self.team.enabled:
            raise RuntimeError("team orchestration is disabled")
        from adaptive_harness.orchestration.team import team_worthwhile

        if not team_worthwhile(root_goal, success_criteria):
            return await super().execute(
                root_goal=root_goal,
                success_criteria=success_criteria,
                constraints=constraints,
                max_agents=max_agents,
                max_cost_usd=max_cost_usd,
            )

        if self.policy_arena is not None:
            self._active_policy = self.policy_arena.champion()
            self._challengers = self.policy_arena.challengers()
        else:
            self._active_policy = ComputePolicy("balanced-v1", "balanced", {}, "champion")
            self._challengers = []

        cap = min(max_agents or self.team.max_agents, self.team.max_agents)
        self._active_budget_usd = max_cost_usd
        self._spent_usd = 0.0
        self._current_confidence = 0.0
        self._raw_confidence = 0.0
        self._evidence_strength = 0.0
        self._child_attempts = 0
        self._child_attempt_cap = cap
        self._strategy_records = []
        self._decision_log = []

        plan, total_cost = await self.plan(root_goal, success_criteria, constraints, cap)
        self._spent_usd = total_cost
        tasks = self._select_tasks(plan, cap)
        reports: dict[str, AgentReport] = {}
        executed_count = 0
        rounds = 0
        decision = SynthesisDecision(answer="", confidence=0.0, should_continue=True)
        raw_final_confidence = 0.0

        while rounds < self.team.max_rounds and executed_count < cap:
            rounds += 1
            previous_confidence = self._current_confidence
            records_before = len(self._strategy_records)
            remaining = cap - executed_count
            executed, cost = await self._execute_dag(
                tasks, root_goal, success_criteria, constraints, reports, remaining
            )
            executed_count += len(executed)
            total_cost += cost
            self._spent_usd = total_cost
            remaining = cap - executed_count
            decision, synthesis_cost = await self.synthesize(
                root_goal, success_criteria, constraints, reports, rounds, remaining
            )
            total_cost += synthesis_cost
            self._spent_usd = total_cost

            raw_final_confidence = decision.confidence
            estimate = self.calibrator.calibrate(
                raw_confidence=decision.confidence,
                profile="team",
                reports=list(reports.values()),
                unresolved_count=len(decision.unresolved),
            )
            decision.confidence = estimate.calibrated
            # The base synthesizer may stop on raw self-confidence alone. v0.5 reopens one
            # bounded evidence-focused check when confidence looked sufficient to the model but
            # trajectory evidence is still weak. Hard budget/attempt/round ceilings remain final.
            evidence_gap = (
                raw_final_confidence >= self.team.target_confidence
                and decision.confidence < self.team.target_confidence
                and estimate.evidence_strength < 0.55
                and remaining > 0
                and self._child_attempts < self._child_attempt_cap
                and rounds < self.team.max_rounds
            )
            budget_left = self._budget_remaining()
            budget_can_verify = (
                budget_left is None
                or budget_left >= self.team.economy.cold_start_primary_call_usd
            )
            if evidence_gap and budget_can_verify and not decision.followups:
                decision.should_continue = True
                decision.followups = [
                    FollowUp(
                        task=(
                            "Independently verify the highest-impact unresolved assumptions in the "
                            "current synthesis. Prefer a deterministic workspace postcondition when "
                            "one genuinely applies; otherwise gather independent source evidence. "
                            "Return only evidence that can confirm, refute, or bound the claim."
                        ),
                        profile=None,
                        difficulty=0.58,
                        expected_value=0.92,
                        freshness=Freshness.STABLE,
                    )
                ]
            quality_gain = max(0.0, decision.confidence - previous_confidence)
            self._raw_confidence = raw_final_confidence
            self._current_confidence = max(previous_confidence, decision.confidence)
            self._evidence_strength = max(self._evidence_strength, estimate.evidence_strength)

            new_records = self._strategy_records[records_before:]
            self._record_strategy_learning(new_records, quality_gain)

            stable_run_key = hashlib.sha256(root_goal.encode("utf-8")).hexdigest()[:20]
            self.traces.append(
                TraceEvent(
                    run_id=f"team:{stable_run_key}",
                    kind="compute_market_round",
                    payload={
                        "round": rounds,
                        "raw_confidence": raw_final_confidence,
                        "calibrated_confidence": decision.confidence,
                        "confidence_before": previous_confidence,
                        "quality_gain": quality_gain,
                        "evidence_strength": estimate.evidence_strength,
                        "calibration_reasons": list(estimate.reasons),
                        "reported_cost_usd": total_cost,
                        "child_attempts": self._child_attempts,
                        "policy_id": self._active_policy.id,
                        "decisions": self._decision_log[-max(1, len(new_records) * 3):],
                    },
                )
            )

            if max_cost_usd is not None and total_cost >= max_cost_usd:
                decision.should_continue = False
            # A raw high-confidence answer is not enough for an early stop if the synthesizer itself
            # requested continuation and calibrated evidence remains below the target.
            if (
                not decision.should_continue
                or not decision.followups
                or remaining <= 0
                or self._child_attempts >= self._child_attempt_cap
            ):
                break
            tasks = [
                WorkItem(
                    id=f"followup-{rounds}-{i+1}",
                    task=followup.task,
                    profile=followup.profile,
                    difficulty=followup.difficulty,
                    expected_value=followup.expected_value,
                    critical=True,
                    freshness=followup.freshness,
                    cacheable=True,
                )
                for i, followup in enumerate(decision.followups[:remaining])
            ]

        cached = sum(1 for report in reports.values() if report.status == "cached")
        references = sum(
            1 for report in reports.values() if report.cache_status == "semantic_reference"
        )
        action_counts = Counter(record.bid.action.value for record in self._strategy_records)
        recommendations = []
        if self.policy_arena is not None:
            recommendations = [
                {
                    "challenger": item.challenger_id,
                    "matched_trials": item.matched_trials,
                    "promotable": item.promotable,
                    "reason": item.reason,
                }
                for item in self.policy_arena.recommendations()
            ]

        metadata = {
            "model_cost_usd": total_cost,
            "team_agents_executed": executed_count,
            "team_child_attempts": self._child_attempts,
            "team_rounds": rounds,
            "team_cache_exact_hits": cached,
            "team_cache_reference_hits": references,
            "team_raw_confidence": raw_final_confidence,
            "team_confidence": decision.confidence,
            "team_evidence_strength": self._evidence_strength,
            "team_unresolved": decision.unresolved,
            "plan_rationale": plan.rationale,
            "compute_market_policy": self._active_policy.id,
            "compute_market_actions": dict(action_counts),
            "compute_market_decisions": self._decision_log,
            "policy_arena_recommendations": recommendations,
        }
        return ToolExecutionResult(
            content=decision.answer,
            metadata=metadata,
            trust=TrustLevel.UNTRUSTED_EXTERNAL,
        )
