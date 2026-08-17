from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich import print

from adaptive_harness import cli as legacy_cli
from adaptive_harness import v06_cli as v06
from adaptive_harness.contracts import RiskLevel, ToolExecutionResult, ToolSpec, TrustLevel
from adaptive_harness.orchestration.cell_runtime import (
    CellLimits,
    CellPlan,
    HierarchicalCellRuntime,
)
from adaptive_harness.orchestration.distributed_control import DistributedControlStore
from adaptive_harness.orchestration.hierarchical_service import (
    LLMHierarchicalService,
    hierarchy_worthwhile,
    register_hierarchical_orchestration,
)
from adaptive_harness.orchestration.mission_dispatcher import MissionDispatcher
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.tools.builtin import register_builtin_tools
from adaptive_harness.tools.evidence_tools import register_evidence_tools
from adaptive_harness.tools.state_tools import register_state_tools
from adaptive_harness.v07_config import HarnessConfig

app = v06.app


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _distributed_components(cfg: HarnessConfig, provider, traces, runtime):
    if not cfg.distributed_reasoning.enabled:
        return None, None, None

    dist = cfg.distributed_reasoning
    control = DistributedControlStore(
        _resolved_path(cfg.harness_root, dist.control_db)
    )
    cells = HierarchicalCellRuntime(
        control,
        limits=CellLimits(
            max_depth=dist.max_depth,
            max_cells=dist.max_cells,
            max_leaf_attempts=dist.max_leaf_attempts,
            max_children_per_cell=dist.max_children_per_cell,
            min_child_budget_usd=dist.min_child_budget_usd,
        ),
    )

    # Dedicated leaf runtime: same provider/traces/memory/profiles, reduced capability surface.
    child_tools = ToolRegistry()
    register_builtin_tools(child_tools, cfg.workspace)
    register_evidence_tools(child_tools, cfg.workspace)
    child_tools.unregister("fs_write")
    child_tools.unregister("sandbox_command")

    state_store = None
    if cfg.long_horizon.enabled:
        state_store = v06._state_store(cfg)
        register_state_tools(
            child_tools,
            store=state_store,
            traces=traces,
            context_builder=v06.MissionContextBuilder(),
            writable=False,
            default_context_tokens=cfg.long_horizon.context_budget_tokens,
            fact_evidence_strength=cfg.long_horizon.fact_evidence_strength,
        )

    child_scopes = {
        scope
        for scope in cfg.allowed_scopes
        if scope in {"fs:read", "net:read", "exec:sandbox"}
    }
    child_cfg = cfg.model_copy(update={"allowed_scopes": child_scopes})
    child_runtime = AgentRuntime(
        config=child_cfg,
        provider=provider,
        tools=child_tools,
        traces=traces,
        memory=runtime.memory,
        skills=runtime.skills,
        profiles=runtime.profiles,
    )
    service = LLMHierarchicalService(
        config=cfg,
        provider=provider,
        child_runner=child_runtime.run,
        cells=cells,
    )
    register_hierarchical_orchestration(
        runtime.tools,
        service,
        default_budget_usd=dist.default_hierarchy_budget_usd,
    )

    # Durable missions often contain many small tasks. Paying a manager-model call for every flat
    # task would defeat the point of hierarchical economics, so mission planning gets the same
    # deterministic zero-token structural gate as the explicit hierarchy tool.
    async def mission_planner(spec, depth, max_children, allowance_usd):
        if not hierarchy_worthwhile(
            spec.task,
            difficulty=spec.difficulty,
            min_difficulty=dist.hierarchy_min_difficulty,
            min_goal_chars=dist.hierarchy_min_goal_chars,
        ):
            return CellPlan(
                children=[],
                planner_cost_usd=0.0,
                rationale="zero-token hierarchy gate selected direct leaf execution",
            )
        return await service.planner(spec, depth, max_children, allowance_usd)

    dispatcher = None
    if state_store is not None:
        dispatcher = MissionDispatcher(
            state=state_store,
            control=control,
            traces=traces,
            cells=cells,
            context_builder=v06.MissionContextBuilder(),
            lease_ttl_seconds=dist.lease_ttl_seconds,
            context_budget_tokens=cfg.long_horizon.context_budget_tokens,
        )

        async def dispatch_next(args: dict[str, Any]) -> ToolExecutionResult:
            mission_id = str(args["mission_id"])
            expected_revision = int(args["expected_revision"])
            current = state_store.snapshot(mission_id)
            if current.revision != expected_revision:
                raise ValueError(
                    f"stale mission revision: expected {expected_revision}, current {current.revision}; "
                    "read mission state again before dispatching"
                )
            result = await dispatcher.dispatch_one(
                mission_id,
                owner=f"parent-dispatch:{mission_id}",
                budget_usd=float(args.get("budget_usd", dist.default_dispatch_budget_usd)),
                planner=mission_planner,
                leaf_runner=service.leaf_runner,
                context_budget_tokens=(
                    int(args["context_budget_tokens"])
                    if args.get("context_budget_tokens") is not None
                    else None
                ),
            )
            return ToolExecutionResult(
                content=result.model_dump_json(indent=2),
                metadata={
                    "model_cost_usd": result.cost_usd,
                    "dispatch_status": result.status.value,
                    "mission_id": mission_id,
                    "task_id": result.task_id,
                    "evidence_refs": result.evidence_refs,
                },
                trust=TrustLevel.TOOL,
            )

        runtime.tools.register(
            ToolSpec(
                name="mission_dispatch_next",
                description=(
                    "Execute exactly one dependency-ready durable mission task under a recoverable "
                    "lease, bounded context and hierarchical dollar escrow. Requires the exact "
                    "mission revision just read by the parent. Completion is evidence-gated."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "mission_id": {"type": "string", "minLength": 1},
                        "expected_revision": {"type": "integer", "minimum": 0},
                        "budget_usd": {"type": "number", "minimum": 0},
                        "context_budget_tokens": {"type": "integer", "minimum": 64},
                    },
                    "required": ["mission_id", "expected_revision"],
                    "additionalProperties": False,
                },
                risk=RiskLevel.REVERSIBLE_WRITE,
                required_scopes={"fs:write"},
                # The execution ledger binds the exact mission revision. The next dispatch must use
                # a newly-read revision, so identical retries can be deduplicated safely.
                idempotent=False,
                source="local_orchestration",
            ),
            dispatch_next,
        )

    # Expose internal v0.7 components to CLI commands without changing legacy build's 4-tuple API.
    runtime._distributed_control = control
    runtime._hierarchical_service = service
    runtime._mission_dispatcher = dispatcher
    runtime._mission_planner = mission_planner
    return control, service, dispatcher


def build(config_path: str):
    # v0.6 remains the source of truth for the base governed runtime; v0.7 adds a bounded cognition
    # plane around it rather than replacing approvals/evidence/capability policy.
    _base_cfg, provider, traces, runtime = v06.build(config_path)
    cfg = HarnessConfig.from_yaml(config_path)
    # Use the richer config for subsequent Goal budget/routing decisions on the parent as well.
    runtime.config = cfg
    _distributed_components(cfg, provider, traces, runtime)
    return cfg, provider, traces, runtime


legacy_cli.build = build


@app.command("mission-dispatch")
def mission_dispatch(
    mission_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    budget_usd: float | None = typer.Option(None, "--budget-usd"),
):
    """Execute one durable mission task using leases, escrow and evidence-gated cells."""
    cfg, _provider, _traces, runtime = build(config)
    dispatcher = getattr(runtime, "_mission_dispatcher", None)
    service = getattr(runtime, "_hierarchical_service", None)
    mission_planner = getattr(runtime, "_mission_planner", None)
    if dispatcher is None or service is None or mission_planner is None:
        raise typer.BadParameter("distributed reasoning / long_horizon is disabled")

    async def execute():
        return await dispatcher.dispatch_one(
            mission_id,
            owner="cli-mission-dispatch",
            budget_usd=(
                budget_usd
                if budget_usd is not None
                else cfg.distributed_reasoning.default_dispatch_budget_usd
            ),
            planner=mission_planner,
            leaf_runner=service.leaf_runner,
        )

    import asyncio

    result = asyncio.run(execute())
    print(result.model_dump_json(indent=2))


@app.command("distributed-status")
def distributed_status(
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Show hard v0.7 cell/lease/escrow ceilings."""
    cfg = HarnessConfig.from_yaml(config)
    print(json.dumps(cfg.distributed_reasoning.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    app()
