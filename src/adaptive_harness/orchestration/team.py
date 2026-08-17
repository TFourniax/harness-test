from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from adaptive_harness.config import HarnessConfig, ModelRole
from adaptive_harness.contracts import Goal, RiskLevel, ToolExecutionResult, ToolSpec, TrustLevel
from adaptive_harness.orchestration.contracts import (
    AgentReport,
    ContextCapsule,
    FollowUp,
    Freshness,
    SynthesisDecision,
    WorkItem,
    WorkPlan,
)
from adaptive_harness.orchestration.router import CostAwareModelRouter
from adaptive_harness.orchestration.routing_stats import RoutingStatsStore
from adaptive_harness.orchestration.vector_cache import SemanticWorkCache
from adaptive_harness.providers.base import ModelProvider
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore

ChildRunner = Callable[[Goal], Awaitable[RunResult]]


def team_worthwhile(root_goal: str, success_criteria: list[str]) -> bool:
    text = root_goal.lower()
    complex_terms = {
        "audit", "research", "recherche", "compare", "analyse", "analyze", "debug",
        "architecture", "repo", "repository", "scrape", "market", "benchmark", "multi",
        "parallel", "investigate", "enquête", "strategy", "stratégie", "plan complet",
    }
    if success_criteria and len(success_criteria) >= 2:
        return True
    if any(term in text for term in complex_terms):
        return True
    if len(root_goal) >= 220:
        return True
    conjunctions = sum(text.count(token) for token in (" and ", " et ", ";", " puis ", " then "))
    return conjunctions >= 2


def workspace_fingerprint(workspace: str | Path) -> str:
    """Cheap snapshot identity for cache isolation.

    Git checkouts use HEAD + dirty status; non-Git workspaces fall back to a bounded metadata hash.
    It is a cache namespace, not a cryptographic proof of workspace contents.
    """

    root = Path(workspace).resolve()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, timeout=2
        )
        if head.returncode == 0:
            dirty = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                text=True,
                capture_output=True,
                timeout=3,
            )
            payload = f"git:{head.stdout.strip()}:{hashlib.sha256(dirty.stdout.encode()).hexdigest()}"
            return hashlib.sha256(payload.encode()).hexdigest()
    except (OSError, subprocess.SubprocessError):
        pass

    h = hashlib.sha256(str(root).encode())
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= 4000:
            break
        if not path.is_file() or any(part in {".git", ".harness", "node_modules", ".venv"} for part in path.parts):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        h.update(str(path.relative_to(root)).encode())
        h.update(f":{stat.st_size}:{stat.st_mtime_ns}".encode())
        count += 1
    return h.hexdigest()


def _json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
        return None


class TeamOrchestrator:
    """Cost-aware sparse multi-agent loop exposed as one parent-agent tool.

    The parent remains the authority and executor. The team tool decomposes cognition/research into a
    DAG, runs independent specialists with minimal context, synthesizes, and only creates follow-up
    work when the previous round leaves material uncertainty.
    """

    def __init__(
        self,
        *,
        config: HarnessConfig,
        provider: ModelProvider,
        traces: TraceStore,
        child_runner: ChildRunner,
        cache: SemanticWorkCache | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.traces = traces
        self.child_runner = child_runner
        self.team = config.team
        stats = None
        if self.team is not None:
            stats_path = Path(self.team.routing_stats_db)
            if not stats_path.is_absolute():
                stats_path = Path(config.harness_root) / stats_path
            stats = RoutingStatsStore(stats_path)
        self.router = CostAwareModelRouter(config, stats)
        self.cache = cache
        self.context_fingerprint = workspace_fingerprint(config.workspace)

    async def _complete_json(
        self, role: ModelRole, system: str, user: str, *, max_tokens: int
    ) -> tuple[dict[str, Any] | None, str, float]:
        turn = await self.provider.complete(
            model=role.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[],
            temperature=0.0,
            max_tokens=min(max_tokens, role.max_tokens),
            tool_mode=role.tool_mode,
            fallbacks=role.fallbacks,
            timeout=role.timeout,
            num_retries=role.num_retries,
        )
        content = turn.content or ""
        cost = float(turn.usage.get("cost_usd", 0.0) or 0.0)
        return _json_object(content), content, cost

    async def plan(
        self, root_goal: str, success_criteria: list[str], constraints: list[str], max_agents: int
    ) -> tuple[WorkPlan, float]:
        plan_text = root_goal + "\n" + json.dumps(success_criteria, sort_keys=True) + "\n" + json.dumps(constraints, sort_keys=True)
        plan_hint = None
        if self.cache is not None:
            hit = self.cache.lookup(
                namespace="team-control",
                kind="plan",
                semantic_text=plan_text,
                context_fingerprint=self.context_fingerprint,
                direct_threshold=1.0,
                reference_threshold=self.team.semantic_reference_threshold,
                allow_semantic_direct=False,
            )
            if hit.status == "exact" and hit.value:
                try:
                    cached_plan = WorkPlan.model_validate(hit.value)
                    cached_plan.validate_dag()
                    return cached_plan, 0.0
                except Exception:
                    pass
            if hit.status == "semantic_reference" and hit.value:
                plan_hint = json.dumps(hit.value, ensure_ascii=False)[:5000]

        route = self.router.orchestrator()
        system = (
            "You are a cost-aware team orchestrator. Build a sparse DAG of specialist work for a "
            "parent execution agent. Spawn agents only when parallel or independent reasoning has "
            "material expected value. Avoid broadcast collaboration: each worker receives only its "
            "dependencies. Prefer 2-4 useful workers over many redundant workers. Use redundancy only "
            "for genuinely high-uncertainty/critical claims. External side effects must remain with the "
            "parent agent; workers analyze, inspect, research, or propose. Return JSON only."
        )
        user = (
            f"ROOT GOAL:\n{root_goal}\n\nSUCCESS CRITERIA:\n{json.dumps(success_criteria)}\n"
            f"CONSTRAINTS:\n{json.dumps(constraints)}\nMAX EXECUTED WORKERS: {max_agents}\n"
            f"MAX PLAN TASKS: {self.team.max_tasks_per_plan}\n"
            + (f"\nPRIOR ANALOGOUS PLAN (hint only; adapt, do not copy blindly):\n{plan_hint}\n" if plan_hint else "")
            + "\nSchema: {\"rationale\":str,\"tasks\":[{\"id\":str,\"task\":str,"
            "\"dependencies\":[str],\"profile\":null|\"generic\"|\"code\"|\"research\"|"
            "\"scraping\"|\"operations\"|\"advisory\",\"difficulty\":0..1,"
            "\"expected_value\":0..1,\"critical\":bool,\"freshness\":"
            "\"immutable\"|\"stable\"|\"volatile\",\"redundancy_group\":null|str,"
            "\"cacheable\":bool}]}"
        )
        payload, raw, cost = await self._complete_json(route.role, system, user, max_tokens=2200)
        if payload is None:
            return WorkPlan(
                rationale="planner protocol fallback",
                tasks=[
                    WorkItem(
                        id="task-1",
                        task=root_goal,
                        expected_value=1.0,
                        critical=True,
                        freshness=Freshness.STABLE,
                    )
                ],
            ), cost
        try:
            plan = WorkPlan.model_validate(payload)
            plan.tasks = plan.tasks[: self.team.max_tasks_per_plan]
            plan.validate_dag()
            if not plan.tasks:
                raise ValueError("empty team plan")
            if self.cache is not None:
                self.cache.put(
                    namespace="team-control",
                    kind="plan",
                    semantic_text=plan_text,
                    context_fingerprint=self.context_fingerprint,
                    value=plan.model_dump(mode="json"),
                    verified=True,
                    allow_direct=False,
                    ttl_seconds=self.team.stable_cache_ttl_seconds,
                )
            return plan, cost
        except Exception:
            return WorkPlan(
                rationale=f"invalid planner output fallback: {raw[:240]}",
                tasks=[WorkItem(id="task-1", task=root_goal, expected_value=1.0, critical=True)],
            ), cost

    def _select_tasks(self, plan: WorkPlan, max_agents: int) -> list[WorkItem]:
        if len(plan.tasks) <= max_agents:
            return plan.tasks
        by_id = {task.id: task for task in plan.tasks}
        keep: set[str] = {task.id for task in plan.tasks if task.critical}

        def add_deps(task_id: str) -> None:
            for dep in by_id[task_id].dependencies:
                if dep not in keep:
                    keep.add(dep)
                    add_deps(dep)

        for task_id in list(keep):
            add_deps(task_id)
        ranked = sorted(
            plan.tasks,
            key=lambda task: (task.critical, task.expected_value - 0.25 * task.difficulty),
            reverse=True,
        )
        for task in ranked:
            if len(keep) >= max_agents:
                break
            keep.add(task.id)
            add_deps(task.id)
        # If ancestor expansion crosses the cap, correctness beats a hard cap; trim only optional leaves.
        selected = [task for task in plan.tasks if task.id in keep]
        return selected[: max(max_agents, len([t for t in selected if t.critical]))]

    @staticmethod
    def _direct_cache_safe(task: WorkItem, result: RunResult) -> bool:
        if task.freshness == Freshness.VOLATILE:
            return False
        if result.status.value != "succeeded" or not result.observations:
            return False
        # Direct reuse requires concrete trusted observations, not a model-only assertion. Any
        # untrusted/external observation makes the result reference-only.
        return all(
            obs.ok and obs.trust != TrustLevel.UNTRUSTED_EXTERNAL
            for obs in result.observations
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
                "You are a specialist worker. Do not execute externally visible side effects; return analysis/evidence to the parent.",
            ],
            dependency_summaries=deps,
            reference_hint=reference_hint,
        )
        route = self.router.for_worker(task)
        try:
            result = await self.child_runner(
                Goal(
                    text=capsule.render(),
                    profile=task.profile,
                    max_steps=self.team.worker_max_steps,
                    model_role=route.name,
                )
            )
        except Exception as exc:
            return AgentReport(
                task_id=task.id,
                answer=f"Worker failed before producing a result: {type(exc).__name__}: {exc}",
                confidence=0.0,
                role=route.name,
                status="failed",
                cache_status="semantic_reference" if reference_hint else "miss",
            )

        succeeded = result.status.value == "succeeded"
        confidence = 0.76 if succeeded else 0.1
        if succeeded and result.answer and any(token in result.answer.lower() for token in ("uncertain", "unknown", "cannot verify", "not sure")):
            confidence = 0.58
        report = AgentReport(
            task_id=task.id,
            answer=result.answer or f"Worker ended with status {result.status.value}",
            confidence=confidence,
            role=route.name,
            status="succeeded" if succeeded else "failed",
            cost_usd=result.reported_cost_usd,
            cache_status="semantic_reference" if reference_hint else "miss",
            evidence_refs=[obs.call_id for obs in result.observations if obs.ok],
        )
        self.router.record_worker(
            task, route, succeeded=succeeded, cost_usd=result.reported_cost_usd
        )
        if self.cache is not None and task.cacheable and succeeded:
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
                verified=self._direct_cache_safe(task, result),
                allow_direct=self._direct_cache_safe(task, result),
                ttl_seconds=ttl,
            )
        return report

    async def _execute_dag(
        self,
        tasks: list[WorkItem],
        root_goal: str,
        success_criteria: list[str],
        constraints: list[str],
        reports: dict[str, AgentReport],
        remaining_slots: int,
    ) -> tuple[list[WorkItem], float]:
        pending = {task.id: task for task in tasks}
        executed: list[WorkItem] = []
        total_cost = 0.0
        while pending and remaining_slots > 0:
            ready = [
                task
                for task in pending.values()
                if all(dep in reports for dep in task.dependencies)
            ]
            if not ready:
                # Broken/cyclic plans should never deadlock the parent.
                for task in pending.values():
                    reports[task.id] = AgentReport(
                        task_id=task.id,
                        answer="Skipped because dependencies never became available.",
                        confidence=0.0,
                        status="skipped",
                    )
                break
            ready = sorted(
                ready,
                key=lambda task: (task.critical, task.expected_value - 0.25 * task.difficulty),
                reverse=True,
            )
            wave = []
            for task in ready:
                utility = task.expected_value - 0.25 * task.difficulty
                if not task.critical and utility < self.team.utility_floor:
                    reports[task.id] = AgentReport(
                        task_id=task.id,
                        answer=f"Skipped by marginal-utility gate ({utility:.2f} < {self.team.utility_floor:.2f}).",
                        confidence=0.0,
                        status="skipped",
                    )
                    del pending[task.id]
                    continue
                wave.append(task)
                if len(wave) >= min(self.config.max_parallel_agents, remaining_slots):
                    break
            if not wave:
                continue
            results = await asyncio.gather(
                *(
                    self._run_item(task, root_goal, success_criteria, constraints, reports)
                    for task in wave
                )
            )
            for task, report in zip(wave, results):
                reports[task.id] = report
                executed.append(task)
                total_cost += report.cost_usd
                del pending[task.id]
                remaining_slots -= 1
        return executed, total_cost

    async def synthesize(
        self,
        root_goal: str,
        success_criteria: list[str],
        constraints: list[str],
        reports: dict[str, AgentReport],
        round_index: int,
        remaining_slots: int,
    ) -> tuple[SynthesisDecision, float]:
        route = self.router.synthesizer()
        compact = "\n\n".join(
            report.compact(self.team.max_report_chars) for report in reports.values()
        )
        system = (
            "You are the lead orchestrator. Evaluate specialist reports, reconcile disagreements, "
            "and answer the root goal. Do not reward agreement by itself: prefer evidence. If a "
            "material gap remains and another targeted specialist has positive expected value, request "
            "a follow-up. Otherwise stop early. Return JSON only."
        )
        user = (
            f"ROOT GOAL:\n{root_goal}\nSUCCESS CRITERIA:{json.dumps(success_criteria)}\n"
            f"CONSTRAINTS:{json.dumps(constraints)}\nROUND:{round_index}\n"
            f"REMAINING WORKER SLOTS:{remaining_slots}\n\nREPORTS:\n{compact}\n\n"
            "Schema: {\"answer\":str,\"confidence\":0..1,\"should_continue\":bool,"
            "\"unresolved\":[str],\"followups\":[{\"task\":str,\"profile\":null|str,"
            "\"difficulty\":0..1,\"expected_value\":0..1,\"freshness\":"
            "\"immutable\"|\"stable\"|\"volatile\"}]}"
        )
        payload, raw, cost = await self._complete_json(route.role, system, user, max_tokens=3600)
        if payload is None:
            return SynthesisDecision(
                answer=raw.strip() or "Team synthesis failed to produce an answer.",
                confidence=0.5,
                should_continue=False,
            ), cost
        try:
            decision = SynthesisDecision.model_validate(payload)
        except Exception:
            return SynthesisDecision(answer=raw.strip(), confidence=0.5), cost
        if remaining_slots <= 0 or decision.confidence >= self.team.target_confidence and not decision.unresolved:
            decision.should_continue = False
            decision.followups = []
        return decision, cost

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
        if not team_worthwhile(root_goal, success_criteria):
            return ToolExecutionResult(
                content=(
                    "TEAM_NOT_JUSTIFIED: the request does not currently show enough decomposable "
                    "complexity to justify multi-agent overhead. Continue with the parent agent and "
                    "call the team again only if material independent branches emerge."
                ),
                metadata={
                    "model_cost_usd": 0.0,
                    "team_agents_executed": 0,
                    "team_rounds": 0,
                    "team_gate": "declined_low_complexity",
                },
                trust=TrustLevel.TOOL,
            )
        cap = min(max_agents or self.team.max_agents, self.team.max_agents)
        plan, total_cost = await self.plan(root_goal, success_criteria, constraints, cap)
        tasks = self._select_tasks(plan, cap)
        reports: dict[str, AgentReport] = {}
        executed_count = 0
        rounds = 0
        decision = SynthesisDecision(answer="", confidence=0.0, should_continue=True)

        while rounds < self.team.max_rounds and executed_count < cap:
            rounds += 1
            remaining = cap - executed_count
            executed, cost = await self._execute_dag(
                tasks, root_goal, success_criteria, constraints, reports, remaining
            )
            executed_count += len(executed)
            total_cost += cost
            remaining = cap - executed_count
            decision, synthesis_cost = await self.synthesize(
                root_goal, success_criteria, constraints, reports, rounds, remaining
            )
            total_cost += synthesis_cost
            if max_cost_usd is not None and total_cost >= max_cost_usd:
                decision.should_continue = False
            if not decision.should_continue or not decision.followups or remaining <= 0:
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
        references = sum(1 for report in reports.values() if report.cache_status == "semantic_reference")
        metadata = {
            "model_cost_usd": total_cost,
            "team_agents_executed": executed_count,
            "team_rounds": rounds,
            "team_cache_exact_hits": cached,
            "team_cache_reference_hits": references,
            "team_confidence": decision.confidence,
            "team_unresolved": decision.unresolved,
            "plan_rationale": plan.rationale,
        }
        return ToolExecutionResult(
            content=decision.answer,
            metadata=metadata,
            # The output is model-synthesized from potentially external observations. Parent treats it
            # as untrusted data until it independently decides what to do with it.
            trust=TrustLevel.UNTRUSTED_EXTERNAL,
        )


def register_team_orchestration(
    registry: ToolRegistry,
    orchestrator: TeamOrchestrator,
) -> None:
    async def team_tool(args: dict[str, Any]) -> ToolExecutionResult:
        return await orchestrator.execute(
            root_goal=args["goal"],
            success_criteria=args.get("success_criteria", []),
            constraints=args.get("constraints", []),
            max_agents=args.get("max_agents"),
            max_cost_usd=args.get("max_cost_usd"),
        )

    registry.register(
        ToolSpec(
            name="team_orchestrate",
            description=(
                "Use a cost-aware specialist team for a genuinely complex decomposable problem. "
                "The internal orchestrator builds a sparse dependency DAG, runs useful workers in "
                "parallel, reuses safe exact/semantic cache entries, progressively escalates model "
                "quality, synthesizes results, and launches targeted follow-ups only when material "
                "uncertainty remains. Prefer this over manual broad delegation. Do not use for trivial "
                "single-step tasks. The team analyzes/researches/proposes; externally visible effects "
                "remain with the parent agent."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "minLength": 1},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "max_agents": {"type": "integer", "minimum": 1, "maximum": 16},
                    "max_cost_usd": {"type": ["number", "null"], "minimum": 0},
                },
                "required": ["goal"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
            required_scopes=set(),
            idempotent=True,
            source="delegate",
        ),
        team_tool,
    )
