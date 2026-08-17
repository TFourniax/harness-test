from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

from adaptive_harness.config import HarnessConfig
from adaptive_harness.contracts import Goal, RunStatus, ToolExecutionResult, TraceEvent, TrustLevel
from adaptive_harness.orchestration.compute_market import (
    ComputeAction,
    ComputeBid,
    ComputeEconomyStore,
    ComputeMarket,
)
from adaptive_harness.orchestration.contracts import (
    AgentReport,
    ContextCapsule,
    Freshness,
    SynthesisDecision,
    WorkItem,
)
from adaptive_harness.orchestration.team import TeamOrchestrator


class AdaptiveTeamOrchestrator(TeamOrchestrator):
    """v0.4 team loop with a persistent value-of-compute market."""

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
        self.market_store = ComputeEconomyStore(economy_path)
        self.market = ComputeMarket(config, self.market_store)
        self._active_budget_usd: float | None = None
        self._spent_usd = 0.0
        self._current_confidence = 0.0
        self._child_attempts = 0
        self._child_attempt_cap = 1
        self._attempt_lock = asyncio.Lock()
        self._strategy_records: list[tuple[WorkItem, ComputeBid, AgentReport, bool]] = []
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
                )
                return stop, 0
            if requested > remaining:
                fallback_action = (
                    ComputeAction.PRIMARY_SINGLE
                    if bid.action == ComputeAction.MIXED_PAIR
                    else ComputeAction.CHEAP_SINGLE
                )
                bid = self.market.quote(
                    fallback_action,
                    task,
                    current_confidence=self._current_confidence,
                    remaining_budget_usd=self._budget_remaining(),
                )
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
                report.status = "cached"
                report.cache_status = "exact"
                report.cost_usd = 0.0
                self._decision_log.append(
                    f"{task.id}: cache_direct similarity={lookup.similarity:.3f}"
                )
                return report

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
        )
        bid, reserved = await self._reserve_attempts(task, bid)
        self._decision_log.append(f"{task.id}: {bid.action.value} {bid.reason}")
        if bid.action == ComputeAction.STOP or reserved == 0:
            return AgentReport(
                task_id=task.id,
                answer=f"Skipped by adaptive compute market: {bid.reason}",
                confidence=self._current_confidence,
                role="cheap",
                status="skipped",
                cache_status="semantic_reference" if reference_hint else "miss",
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

        try:
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
        except Exception as exc:
            return AgentReport(
                task_id=task.id,
                answer=f"Adaptive worker launch failed: {type(exc).__name__}: {exc}",
                confidence=0.0,
                role="primary" if "primary" in roles else "cheap",
                status="failed",
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
            confidence = 0.05
        elif len(roles) == 1:
            confidence = 0.80 if roles[0] == "primary" else 0.72
        elif len(succeeded_results) == len(roles):
            confidence = 0.84 if bid.action == ComputeAction.CHEAP_PAIR else 0.88
        else:
            confidence = 0.64

        joined = "\n\n".join(answers)
        if any(
            token in joined.lower()
            for token in ("uncertain", "unknown", "cannot verify", "not sure")
        ):
            confidence = max(0.05, confidence - 0.12)

        report = AgentReport(
            task_id=task.id,
            answer=joined or "No worker result.",
            confidence=confidence,
            role="primary" if "primary" in roles else "cheap",
            status="succeeded" if succeeded else "failed",
            cost_usd=total_cost,
            cache_status="semantic_reference" if reference_hint else "miss",
            evidence_refs=evidence_refs,
        )
        self._strategy_records.append((task, bid, report, succeeded))

        if self.cache is not None and task.cacheable and succeeded:
            all_direct_safe = (
                len(succeeded_results) == len(roles)
                and all(self._direct_cache_safe(task, result) for result in succeeded_results)
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

        cap = min(max_agents or self.team.max_agents, self.team.max_agents)
        self._active_budget_usd = max_cost_usd
        self._spent_usd = 0.0
        self._current_confidence = 0.0
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
            quality_gain = max(0.0, decision.confidence - previous_confidence)
            self._current_confidence = max(previous_confidence, decision.confidence)

            new_records = self._strategy_records[records_before:]
            if new_records:
                weight_total = sum(max(0.05, rec[2].confidence) for rec in new_records)
                for task, bid, report, operational_success in new_records:
                    share = quality_gain * max(0.05, report.confidence) / weight_total
                    self.market.record(
                        bid,
                        task,
                        succeeded=(
                            operational_success
                            and (share > 0 or decision.confidence >= self.team.target_confidence)
                        ),
                        cost_usd=report.cost_usd,
                        quality_gain=share,
                    )

            self.traces.append(
                TraceEvent(
                    run_id=f"team:{abs(hash(root_goal))}",
                    kind="compute_market_round",
                    payload={
                        "round": rounds,
                        "confidence_before": previous_confidence,
                        "confidence_after": decision.confidence,
                        "quality_gain": quality_gain,
                        "reported_cost_usd": total_cost,
                        "child_attempts": self._child_attempts,
                        "decisions": self._decision_log[-max(1, len(new_records)):],
                    },
                )
            )

            if max_cost_usd is not None and total_cost >= max_cost_usd:
                decision.should_continue = False
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
        action_counts = Counter(bid.action.value for _, bid, _, _ in self._strategy_records)
        metadata = {
            "model_cost_usd": total_cost,
            "team_agents_executed": executed_count,
            "team_child_attempts": self._child_attempts,
            "team_rounds": rounds,
            "team_cache_exact_hits": cached,
            "team_cache_reference_hits": references,
            "team_confidence": decision.confidence,
            "team_unresolved": decision.unresolved,
            "plan_rationale": plan.rationale,
            "compute_market_actions": dict(action_counts),
            "compute_market_decisions": self._decision_log,
        }
        return ToolExecutionResult(
            content=decision.answer,
            metadata=metadata,
            trust=TrustLevel.UNTRUSTED_EXTERNAL,
        )
