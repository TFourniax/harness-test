from __future__ import annotations

import json
import re
from typing import Any

from adaptive_harness.contracts import (
    Goal,
    RiskLevel,
    RunStatus,
    ToolExecutionResult,
    ToolSpec,
    TrustLevel,
)
from adaptive_harness.orchestration.cell_runtime import (
    CellExecutionResult,
    CellPlan,
    CellSpec,
    HierarchicalCellRuntime,
    LeafOutcome,
)
from adaptive_harness.providers.base import ModelProvider
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.v07_config import HarnessConfig


def _json_object(text: str) -> dict[str, Any] | None:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(value[start : end + 1])
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def hierarchy_worthwhile(
    root_goal: str,
    *,
    difficulty: float,
    min_difficulty: float,
    min_goal_chars: int,
) -> bool:
    """Zero-token gate: recursion requires stronger evidence of structure than a flat team."""
    text = root_goal.lower()
    structural_terms = (
        "architecture",
        "end-to-end",
        "end to end",
        "multi-stage",
        "multi stage",
        "multiple systems",
        "across the repo",
        "whole repo",
        "deep research",
        "comprehensive audit",
        "plan complet",
        "de bout en bout",
        "plusieurs systèmes",
    )
    structural_hits = sum(term in text for term in structural_terms)
    separators = sum(text.count(token) for token in (";", " then ", " puis ", " and ", " et "))
    return bool(
        difficulty >= min_difficulty
        and (
            len(root_goal) >= min_goal_chars
            or structural_hits >= 1
            or separators >= 3
        )
    )


class LLMHierarchicalService:
    """Adapts the existing governed provider/child runtime to bounded reasoning cells.

    Cell leads receive no capability authority. Their planner call has no tools. Leaf workers use the
    existing child AgentRuntime and therefore inherit its profile, capability, evidence and cost gates.
    """

    def __init__(
        self,
        *,
        config: HarnessConfig,
        provider: ModelProvider,
        child_runner,
        cells: HierarchicalCellRuntime,
    ) -> None:
        self.config = config
        self.provider = provider
        self.child_runner = child_runner
        self.cells = cells
        # Optional v0.8 hook. None preserves the exact v0.7 single-leaf behavior.
        self.panel_leaf_runner = None

    def _planner_role(self):
        team = self.config.team
        if (
            team is not None
            and team.orchestrator_role == "cheap"
            and self.config.cheap is not None
        ):
            return self.config.cheap
        return self.config.primary

    def _leaf_role_name(self, spec: CellSpec) -> str:
        if self.config.cheap is None:
            return "primary"
        threshold = 0.82
        if self.config.team is not None and self.config.team.economy is not None:
            threshold = self.config.team.economy.primary_preferred_difficulty
        if spec.critical or spec.difficulty >= threshold:
            return "primary"
        return "cheap"

    async def planner(
        self,
        spec: CellSpec,
        depth: int,
        max_children: int,
        allowance_usd: float,
    ) -> CellPlan:
        role = self._planner_role()
        system = (
            "You are a bounded sub-orchestrator inside an agent harness. Decide whether this exact "
            "subtask has 2 or more materially independent branches whose parallel execution is worth "
            "its extra cost. Never decompose merely to create agents. Never broaden scope. Child "
            "tasks must jointly cover the parent task, avoid duplicates, and have no externally "
            "visible side effects. If a single worker is better, return an empty children array. "
            "Return JSON only."
        )
        user = (
            f"CELL ID: {spec.id}\nDEPTH: {depth}\nTASK:\n{spec.task}\n\n"
            f"SUCCESS CRITERIA: {json.dumps(spec.success_criteria)}\n"
            f"CONSTRAINTS: {json.dumps(spec.constraints[-4:])}\n"
            f"DIFFICULTY: {spec.difficulty:.2f}\nCRITICAL: {spec.critical}\n"
            f"MAX CHILDREN: {max_children}\nREMAINING ESCROW USD: {allowance_usd:.6f}\n\n"
            "Schema: {\"rationale\":str,\"children\":[{\"task\":str,"
            "\"success_criteria\":[str],\"profile\":null|str,\"difficulty\":0..1,"
            "\"critical\":bool,\"decomposable\":bool,\"budget_weight\":number>0}]}"
        )
        turn = await self.provider.complete(
            model=role.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            tools=[],
            temperature=0.0,
            max_tokens=min(1600, role.max_tokens),
            tool_mode=role.tool_mode,
            fallbacks=role.fallbacks,
            timeout=role.timeout,
            num_retries=role.num_retries,
        )
        cost = float(turn.usage.get("cost_usd", 0.0) or 0.0)
        payload = _json_object(turn.content or "")
        if payload is None:
            return CellPlan(planner_cost_usd=cost, rationale="invalid planner protocol; use leaf")
        raw_children = payload.get("children", [])
        if not isinstance(raw_children, list):
            raw_children = []
        children: list[CellSpec] = []
        seen_tasks: set[str] = set()
        for index, raw in enumerate(raw_children[:max_children], start=1):
            if not isinstance(raw, dict):
                continue
            task = str(raw.get("task", "")).strip()
            normalized = re.sub(r"\s+", " ", task.lower())
            if not task or normalized in seen_tasks:
                continue
            seen_tasks.add(normalized)
            try:
                difficulty = max(0.0, min(1.0, float(raw.get("difficulty", spec.difficulty))))
                child = CellSpec(
                    id=f"{spec.id}.{index}",
                    task=task,
                    success_criteria=[
                        str(item) for item in raw.get("success_criteria", []) if str(item).strip()
                    ][:8],
                    constraints=spec.constraints,
                    profile=(str(raw["profile"]) if raw.get("profile") else spec.profile),
                    difficulty=difficulty,
                    critical=bool(raw.get("critical", False)) or spec.critical,
                    decomposable=(
                        bool(raw.get("decomposable", True))
                        and difficulty >= 0.58
                        and depth + 1 < self.config.distributed_reasoning.max_depth
                    ),
                    budget_weight=max(0.05, float(raw.get("budget_weight", 1.0))),
                )
            except (TypeError, ValueError):
                continue
            children.append(child)
        # A one-child decomposition is worse than a direct leaf and the CellRuntime will reject it;
        # normalize here as well so the decision is visible in the rationale.
        if len(children) < 2:
            children = []
        return CellPlan(
            children=children,
            planner_cost_usd=cost,
            rationale=str(payload.get("rationale", ""))[:1200],
        )

    async def leaf_runner(self, spec: CellSpec, allowance_usd: float) -> LeafOutcome:
        if allowance_usd <= 0:
            return LeafOutcome(answer="No leaf budget remains.", success=False, confidence=0.0)
        role_name = self._leaf_role_name(spec)
        max_steps = self.config.team.worker_max_steps if self.config.team is not None else 18
        prompt = (
            "You are a leaf specialist inside a hierarchical agent system. Solve only this bounded "
            "subtask. Do not broaden scope. Use allowed tools to gather concrete evidence and run "
            "task-relevant verification when possible. Do not claim completion from confidence alone.\n\n"
            f"SUBTASK:\n{spec.task}\n\n"
            "SUCCESS CRITERIA:\n- "
            + "\n- ".join(spec.success_criteria or ["Correctly solve the assigned subtask"])
            + "\n\nCONSTRAINTS:\n- "
            + "\n- ".join(spec.constraints or ["None specified"])
        )
        result: RunResult = await self.child_runner(
            Goal(
                text=prompt,
                profile=spec.profile,
                max_steps=max_steps,
                max_cost_usd=allowance_usd,
                model_role=role_name,
            )
        )
        succeeded = result.status == RunStatus.SUCCEEDED
        confidence = 0.82 if succeeded and role_name == "primary" else 0.73 if succeeded else 0.08
        answer = result.answer or f"leaf ended {result.status.value}"
        lowered = answer.lower()
        if succeeded and any(
            token in lowered for token in ("uncertain", "not sure", "cannot verify", "unknown")
        ):
            confidence = min(confidence, 0.58)
        return LeafOutcome(
            answer=answer,
            success=succeeded,
            cost_usd=max(0.0, float(result.reported_cost_usd)),
            confidence=confidence,
            evidence_refs=list(dict.fromkeys(obs.call_id for obs in result.observations if obs.ok)),
        )

    async def execute(
        self,
        *,
        root_goal: str,
        success_criteria: list[str],
        constraints: list[str],
        profile: str | None,
        difficulty: float,
        critical: bool,
        budget_usd: float,
        owner: str,
    ) -> CellExecutionResult:
        cfg = self.config.distributed_reasoning
        decomposable = hierarchy_worthwhile(
            root_goal,
            difficulty=difficulty,
            min_difficulty=cfg.hierarchy_min_difficulty,
            min_goal_chars=cfg.hierarchy_min_goal_chars,
        )
        root = CellSpec(
            id="root",
            task=root_goal,
            success_criteria=success_criteria,
            constraints=constraints,
            profile=profile,
            difficulty=difficulty,
            critical=critical,
            decomposable=decomposable,
        )
        return await self.cells.execute(
            root,
            owner=owner,
            budget_usd=budget_usd,
            planner=self.planner,
            leaf_runner=self.leaf_runner,
            panel_leaf_runner=self.panel_leaf_runner,
        )


def register_hierarchical_orchestration(
    registry: ToolRegistry,
    service: LLMHierarchicalService,
    *,
    default_budget_usd: float,
) -> None:
    async def hierarchical(args: dict[str, Any]) -> ToolExecutionResult:
        result = await service.execute(
            root_goal=str(args["root_goal"]),
            success_criteria=[str(value) for value in args.get("success_criteria", [])],
            constraints=[str(value) for value in args.get("constraints", [])],
            profile=str(args["profile"]) if args.get("profile") else None,
            difficulty=float(args.get("difficulty", 0.8)),
            critical=bool(args.get("critical", False)),
            budget_usd=float(args.get("budget_usd", default_budget_usd)),
            owner="parent-hierarchical-tool",
        )
        return ToolExecutionResult(
            content=result.model_dump_json(indent=2),
            metadata={
                "model_cost_usd": result.cost_usd,
                "cell_status": result.status.value,
                "leaf_attempts": result.leaf_attempts,
                "cells_opened": result.cells_opened,
                "evidence_refs": result.evidence_refs,
            },
            trust=TrustLevel.UNTRUSTED_EXTERNAL,
        )

    registry.register(
        ToolSpec(
            name="hierarchical_orchestrate",
            description=(
                "Use a bounded hierarchy of independent reasoning cells for deeply decomposable, "
                "high-complexity cognition. Prefer the simpler team_orchestrate or direct work when "
                "recursion is not materially justified. Child cells cannot grant capabilities."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "root_goal": {"type": "string", "minLength": 1},
                    "success_criteria": {"type": "array", "items": {"type": "string"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "profile": {"type": ["string", "null"]},
                    "difficulty": {"type": "number", "minimum": 0, "maximum": 1},
                    "critical": {"type": "boolean"},
                    "budget_usd": {"type": "number", "minimum": 0},
                },
                "required": ["root_goal"],
                "additionalProperties": False,
            },
            risk=RiskLevel.READ,
            required_scopes=set(),
            idempotent=True,
            source="delegate",
        ),
        hierarchical,
    )
