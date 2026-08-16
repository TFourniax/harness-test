from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from adaptive_harness.config import HarnessConfig
from adaptive_harness.contracts import Goal, Observation, RiskLevel, RunStatus, ToolCall, TraceEvent
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.providers.base import ModelProvider
from adaptive_harness.profiles import ProfileRegistry, TaskProfile
from adaptive_harness.runtime.context import ContextAssembler
from adaptive_harness.runtime.evidence import EvidenceGate
from adaptive_harness.runtime.policy import CapabilityPolicy, action_fingerprint
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore
from adaptive_harness.runtime.verifier import BlindActionVerifier
from adaptive_harness.skills import SkillRegistry


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    answer: str | None = None
    approval_id: str | None = None
    observations: list[Observation] = field(default_factory=list)
    reported_cost_usd: float = 0.0


class AgentRuntime:
    def __init__(
        self,
        *,
        config: HarnessConfig,
        provider: ModelProvider,
        tools: ToolRegistry,
        traces: TraceStore,
        memory: MemoryStore,
        evidence: EvidenceGate | None = None,
        skills: SkillRegistry | None = None,
        profiles: ProfileRegistry | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.tools = tools
        self.traces = traces
        self.memory = memory
        self.evidence = evidence or EvidenceGate()
        self.skills = skills
        self.profiles = profiles
        self.policy = CapabilityPolicy(config.allowed_scopes, config.approval_risks)
        self.context = ContextAssembler()
        verifier_cfg = config.verifier or config.primary
        self.verifier = BlindActionVerifier(provider, verifier_cfg)

    async def run(self, goal: Goal) -> RunResult:
        run_id = str(uuid4())
        self.traces.append(TraceEvent(run_id=run_id, kind="run_started", payload=goal.model_dump()))
        selected_profile = self._profile(goal)
        self.traces.append(
            TraceEvent(
                run_id=run_id,
                kind="profile_selected",
                payload={"name": selected_profile.name, "actor_role": selected_profile.actor_role},
            )
        )
        notes: list[str] = []
        reported_cost_usd = 0.0
        if self.config.planner is not None:
            planner = self.config.planner
            try:
                plan_turn = await self.provider.complete(
                    model=planner.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Produce a compact execution plan for another agent. Identify subgoals, "
                                "important uncertainties, evidence to gather before commitments, and likely "
                                "verification. This plan is advisory only: never expand the user's scope, "
                                "permissions, or constraints. Return plain text, no tool calls."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                f"GOAL:\n{goal.text}\n\nSUCCESS CRITERIA:\n"
                                + "\n".join(f"- {x}" for x in goal.success_criteria)
                                + "\n\nCONSTRAINTS:\n"
                                + "\n".join(f"- {x}" for x in goal.constraints)
                            ),
                        },
                    ],
                    tools=[],
                    temperature=planner.temperature,
                    max_tokens=planner.max_tokens,
                    tool_mode=planner.tool_mode,
                    fallbacks=planner.fallbacks,
                    timeout=planner.timeout,
                    num_retries=planner.num_retries,
                )
            except Exception as exc:
                self.traces.append(
                    TraceEvent(
                        run_id=run_id,
                        kind="planner_unavailable",
                        payload={"error_type": type(exc).__name__},
                    )
                )
                notes.append(
                    "PLANNER_ADVISORY_UNAVAILABLE: continue conservatively; do not infer extra permissions."
                )
            else:
                reported_cost_usd += float(plan_turn.usage.get("cost_usd", 0.0) or 0.0)
                self.traces.append(
                    TraceEvent(
                        run_id=run_id,
                        kind="planner_turn",
                        payload={
                            "content": plan_turn.content,
                            "usage": plan_turn.usage,
                            "protocol_error": plan_turn.protocol_error,
                        },
                    )
                )
                budget_result = self._budget_failure(run_id, goal, reported_cost_usd, [])
                if budget_result:
                    return budget_result
                if plan_turn.protocol_error:
                    notes.append(
                        "PLANNER_PROTOCOL_ERROR: ignore planner output and continue conservatively."
                    )
                elif plan_turn.content:
                    notes.append("PLANNER_ADVISORY:\n" + plan_turn.content.strip())
        return await self._loop(run_id, goal, [], notes, 0, reported_cost_usd)

    async def resume(self, run_id: str, approval_id: str) -> RunResult:
        checkpoint = self.traces.load_checkpoint(run_id)
        approval = self.traces.get_approval(approval_id)
        if not checkpoint or not approval:
            raise ValueError("missing checkpoint or approval")
        if approval.run_id != run_id:
            raise ValueError("approval does not belong to run")
        if approval.status == "rejected":
            self.traces.clear_checkpoint(run_id)
            self.traces.append(
                TraceEvent(run_id=run_id, kind="run_cancelled", payload={"approval_id": approval_id})
            )
            return RunResult(
                run_id, RunStatus.CANCELLED, answer="Action rejected by human.",
                reported_cost_usd=float(checkpoint.get("reported_cost_usd", 0.0)),
            )
        if approval.status != "approved":
            return RunResult(
                run_id, RunStatus.WAITING_APPROVAL, approval_id=approval_id,
                reported_cost_usd=float(checkpoint.get("reported_cost_usd", 0.0)),
            )

        goal = Goal.model_validate(checkpoint["goal"])
        observations = [Observation.model_validate(o) for o in checkpoint["observations"]]
        notes = list(checkpoint.get("notes", []))
        call = ToolCall.model_validate(checkpoint["pending_call"])
        spec = self.tools.get(call.name)

        # Recreate the policy decision and require exact action fingerprint stability.
        decision = self.policy.evaluate(run_id, spec, call)
        if not decision.approval or decision.approval.fingerprint != approval.fingerprint:
            raise ValueError("approved action fingerprint no longer matches pending action")

        reported_cost_usd = float(checkpoint.get("reported_cost_usd", 0.0))

        # Re-run independent verification at execution time for high-impact calls.
        if (
            self.config.blind_verification_enabled
            and spec.risk in {RiskLevel.EXTERNAL_SIDE_EFFECT, RiskLevel.PRIVILEGED}
        ):
            verdict = await self.verifier.verify(goal, spec, call)
            reported_cost_usd += verdict.reported_cost_usd
            budget_result = self._budget_failure(run_id, goal, reported_cost_usd, observations)
            if budget_result:
                self.traces.clear_checkpoint(run_id)
                return budget_result
            if not verdict.allow or verdict.confidence < 0.75:
                observations.append(
                    Observation(
                        call_id=call.id,
                        tool_name=call.name,
                        ok=False,
                        content=f"APPROVED BUT BLOCKED by fresh independent verification: {verdict.reason}",
                        metadata={"blocked_by": "blind_verifier", "confidence": verdict.confidence},
                    )
                )
                self.traces.clear_checkpoint(run_id)
                return await self._loop(
                    run_id, goal, observations, notes, int(checkpoint["next_step"]), reported_cost_usd
                )

        obs = await self._execute_guarded(run_id, spec, call)
        observations.append(obs)
        self.evidence.update_from_observation(obs)
        self.traces.append(
            TraceEvent(
                run_id=run_id,
                kind="approved_tool_observation",
                payload={"approval_id": approval_id, **obs.model_dump()},
            )
        )
        self.traces.clear_checkpoint(run_id)
        return await self._loop(
            run_id, goal, observations, notes, int(checkpoint["next_step"]), reported_cost_usd
        )

    def _profile(self, goal: Goal) -> TaskProfile:
        if self.profiles is None:
            return TaskProfile(name="generic", description="fallback")
        return self.profiles.select(goal)

    def _actor_role(self, profile: TaskProfile):
        if profile.actor_role == "cheap" and self.config.cheap is not None:
            return self.config.cheap
        return self.config.primary

    def _session_history(self, goal: Goal) -> list[str]:
        if not goal.session_id:
            return []
        return self.memory.recent(f"session:{goal.session_id}", 8)

    def _record_session_success(self, goal: Goal, answer: str, run_id: str) -> None:
        if not goal.session_id:
            return
        self.memory.put(
            kind=f"session:{goal.session_id}",
            value=f"USER:\n{goal.text}\n\nASSISTANT:\n{answer}",
            source=f"run:{run_id}",
        )

    def _effective_budget(self, goal: Goal) -> float | None:
        return goal.max_cost_usd if goal.max_cost_usd is not None else self.config.max_run_cost_usd

    def _budget_failure(
        self,
        run_id: str,
        goal: Goal,
        reported_cost_usd: float,
        observations: list[Observation],
    ) -> RunResult | None:
        budget = self._effective_budget(goal)
        if budget is None or reported_cost_usd <= budget:
            return None
        self.traces.append(
            TraceEvent(
                run_id=run_id,
                kind="run_failed",
                payload={
                    "reason": "reported model cost budget exhausted",
                    "reported_cost_usd": reported_cost_usd,
                    "budget_usd": budget,
                },
            )
        )
        return RunResult(
            run_id,
            RunStatus.FAILED,
            answer=(
                f"Reported model cost budget exhausted: ${reported_cost_usd:.6f} "
                f"> ${budget:.6f}."
            ),
            observations=observations,
            reported_cost_usd=reported_cost_usd,
        )

    async def _execute_guarded(self, run_id: str, spec, call: ToolCall) -> Observation:
        if spec.idempotent:
            return await self.tools.execute(call)

        fingerprint = action_fingerprint(call)
        existing = self.traces.get_execution(fingerprint)
        if existing:
            if existing["status"] == "completed" and existing.get("observation"):
                obs = Observation.model_validate(existing["observation"])
                obs.metadata = {**obs.metadata, "deduplicated": True, "execution_fingerprint": fingerprint}
                return obs
            return Observation(
                call_id=call.id,
                tool_name=call.name,
                ok=False,
                content=(
                    "BLOCKED: this non-idempotent action has a prior execution with unknown "
                    "completion state. Reconcile external/workspace state before any retry."
                ),
                metadata={
                    "blocked_by": "execution_ledger",
                    "execution_fingerprint": fingerprint,
                    "prior_status": existing["status"],
                },
            )

        claimed = self.traces.begin_execution(
            fingerprint, run_id, call.name, call.model_dump(mode="json")
        )
        if not claimed:
            # Concurrent claimant won the race; recurse into the now-existing state.
            return await self._execute_guarded(run_id, spec, call)
        obs = await self.tools.execute(call)
        self.traces.complete_execution(fingerprint, obs.model_dump(mode="json"))
        obs.metadata = {**obs.metadata, "execution_fingerprint": fingerprint}
        return obs

    async def _loop(
        self,
        run_id: str,
        goal: Goal,
        observations: list[Observation],
        notes: list[str],
        start_step: int,
        reported_cost_usd: float,
    ) -> RunResult:
        profile = self._profile(goal)
        active_specs = [spec for spec in self.tools.specs() if profile.allows_tool(spec)]
        active_tool_names = {spec.name for spec in active_specs}
        actor = self._actor_role(profile)
        for step in range(start_step, goal.max_steps):
            active_skills = (
                [s.body for s in self.skills.retrieve(goal.text, limit=4)] if self.skills else []
            )
            messages = self.context.build(
                goal=goal,
                tools=active_specs,
                observations=observations,
                working_notes=notes,
                procedural_memory=self.memory.recent("procedural", 12),
                active_skills=active_skills,
                conversation_history=self._session_history(goal),
                profile_name=profile.name,
                profile_guidance=profile.guidance,
            )
            try:
                turn = await self.provider.complete(
                    model=actor.model,
                    messages=messages,
                    tools=active_specs,
                    temperature=actor.temperature,
                    max_tokens=actor.max_tokens,
                    tool_mode=actor.tool_mode,
                    fallbacks=actor.fallbacks,
                    timeout=actor.timeout,
                    num_retries=actor.num_retries,
                )
            except Exception as exc:
                self.traces.clear_checkpoint(run_id)
                self.traces.append(
                    TraceEvent(
                        run_id=run_id,
                        kind="run_failed",
                        payload={
                            "reason": "model provider unavailable after configured retry/fallback policy",
                            "error_type": type(exc).__name__,
                        },
                    )
                )
                return RunResult(
                    run_id,
                    RunStatus.FAILED,
                    answer=(
                        "Model provider unavailable after configured retries/fallbacks "
                        f"({type(exc).__name__})."
                    ),
                    observations=observations,
                    reported_cost_usd=reported_cost_usd,
                )
            self.traces.append(
                TraceEvent(
                    run_id=run_id,
                    kind="model_turn",
                    payload={
                        "step": step,
                        "content": turn.content,
                        "tool_calls": [c.model_dump() for c in turn.tool_calls],
                        "usage": turn.usage,
                        "protocol_error": turn.protocol_error,
                    },
                )
            )

            reported_cost_usd += float(turn.usage.get("cost_usd", 0.0) or 0.0)
            budget_result = self._budget_failure(run_id, goal, reported_cost_usd, observations)
            if budget_result:
                self.traces.clear_checkpoint(run_id)
                return budget_result

            if turn.protocol_error:
                obs = Observation(
                    call_id=f"model-protocol-{step}",
                    tool_name="model_gateway",
                    ok=False,
                    content=(
                        "MODEL PROTOCOL ERROR: " + turn.protocol_error + ". Retry this step using "
                        "the required response/tool protocol; do not treat this as task completion."
                    ),
                    metadata={"blocked_by": "model_protocol", "step": step},
                )
                observations.append(obs)
                self.traces.append(
                    TraceEvent(run_id=run_id, kind="model_protocol_error", payload=obs.model_dump())
                )
                continue

            if not turn.tool_calls:
                answer = (turn.content or "").strip()
                self.traces.clear_checkpoint(run_id)
                self._record_session_success(goal, answer, run_id)
                self.traces.append(
                    TraceEvent(run_id=run_id, kind="run_succeeded", payload={"answer": answer})
                )
                return RunResult(
                    run_id, RunStatus.SUCCEEDED, answer, observations=observations,
                    reported_cost_usd=reported_cost_usd,
                )

            for call in turn.tool_calls:
                if call.name not in active_tool_names:
                    obs = Observation(
                        call_id=call.id,
                        tool_name=call.name,
                        ok=False,
                        content=(
                            f"BLOCKED: tool {call.name} is outside task profile {profile.name}. "
                            "Profile routing may reduce capabilities but never expands local policy."
                        ),
                        metadata={"blocked_by": "task_profile", "profile": profile.name},
                    )
                    observations.append(obs)
                    self.traces.append(
                        TraceEvent(run_id=run_id, kind="profile_tool_block", payload=obs.model_dump())
                    )
                    continue
                try:
                    spec = self.tools.get(call.name)
                except KeyError:
                    obs = Observation(
                        call_id=call.id,
                        tool_name=call.name,
                        ok=False,
                        content=f"UNKNOWN TOOL: {call.name}",
                        metadata={"blocked_by": "tool_registry"},
                    )
                    observations.append(obs)
                    self.traces.append(
                        TraceEvent(run_id=run_id, kind="invalid_tool_call", payload=obs.model_dump())
                    )
                    continue
                gaps = (
                    self.evidence.relevant_gaps(spec, call)
                    if self.config.evidence_gate_enabled
                    else []
                )
                if gaps:
                    gap_text = "; ".join(g.description for g in gaps)
                    obs = Observation(
                        call_id=call.id,
                        tool_name=call.name,
                        ok=False,
                        content=f"BLOCKED: evidence required before commitment: {gap_text}",
                        metadata={"blocked_by": "evidence_gate", "gaps": [g.id for g in gaps]},
                    )
                    observations.append(obs)
                    self.traces.append(
                        TraceEvent(run_id=run_id, kind="evidence_block", payload=obs.model_dump())
                    )
                    continue

                if (
                    self.config.blind_verification_enabled
                    and spec.risk in {RiskLevel.EXTERNAL_SIDE_EFFECT, RiskLevel.PRIVILEGED}
                ):
                    verdict = await self.verifier.verify(goal, spec, call)
                    reported_cost_usd += verdict.reported_cost_usd
                    budget_result = self._budget_failure(
                        run_id, goal, reported_cost_usd, observations
                    )
                    if budget_result:
                        self.traces.clear_checkpoint(run_id)
                        return budget_result
                    self.traces.append(
                        TraceEvent(
                            run_id=run_id,
                            kind="blind_verification",
                            payload={"call": call.model_dump(), "verdict": {"allow": verdict.allow, "confidence": verdict.confidence, "reason": verdict.reason}},
                        )
                    )
                    if not verdict.allow or verdict.confidence < 0.75:
                        observations.append(
                            Observation(
                                call_id=call.id,
                                tool_name=call.name,
                                ok=False,
                                content=f"BLOCKED by independent verifier: {verdict.reason}",
                                metadata={
                                    "blocked_by": "blind_verifier",
                                    "confidence": verdict.confidence,
                                },
                            )
                        )
                        continue

                decision = self.policy.evaluate(run_id, spec, call)
                if decision.requires_approval and decision.approval:
                    self.traces.save_approval(decision.approval)
                    self.traces.save_checkpoint(
                        run_id,
                        {
                            "goal": goal.model_dump(mode="json"),
                            "observations": [o.model_dump(mode="json") for o in observations],
                            "notes": notes,
                            "pending_call": call.model_dump(),
                            "next_step": step + 1,
                            "reported_cost_usd": reported_cost_usd,
                        },
                    )
                    self.traces.append(
                        TraceEvent(
                            run_id=run_id,
                            kind="approval_required",
                            payload=decision.approval.model_dump(),
                        )
                    )
                    return RunResult(
                        run_id,
                        RunStatus.WAITING_APPROVAL,
                        approval_id=decision.approval.id,
                        observations=observations,
                        reported_cost_usd=reported_cost_usd,
                    )
                if not decision.allowed:
                    observations.append(
                        Observation(
                            call_id=call.id,
                            tool_name=call.name,
                            ok=False,
                            content=f"BLOCKED by capability policy: {decision.reason}",
                            metadata={"blocked_by": "capability_policy"},
                        )
                    )
                    continue

                obs = await self._execute_guarded(run_id, spec, call)
                observations.append(obs)
                self.evidence.update_from_observation(obs)
                self.traces.append(
                    TraceEvent(run_id=run_id, kind="tool_observation", payload=obs.model_dump())
                )

        self.traces.append(
            TraceEvent(run_id=run_id, kind="run_failed", payload={"reason": "step budget exhausted"})
        )
        return RunResult(
            run_id, RunStatus.FAILED, answer="Step budget exhausted", observations=observations,
            reported_cost_usd=reported_cost_usd,
        )
