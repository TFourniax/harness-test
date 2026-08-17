from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print

from adaptive_harness import cli as legacy_cli
from adaptive_harness import v05_cli as v05
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.orchestration.adaptive_team import AdaptiveTeamOrchestrator
from adaptive_harness.orchestration.compute_market import ComputeMarketStore
from adaptive_harness.orchestration.confidence import (
    ConfidenceCalibrationStore,
    TrajectoryConfidenceCalibrator,
)
from adaptive_harness.orchestration.context_budget import MissionContextBuilder
from adaptive_harness.orchestration.multispace_cache import MultiSpaceSemanticWorkCache
from adaptive_harness.orchestration.policy_arena import PolicyArenaStore
from adaptive_harness.orchestration.state_plane import TaskStateStore
from adaptive_harness.orchestration.team import TeamOrchestrator, register_team_orchestration
from adaptive_harness.orchestration.vector_cache import SemanticWorkCache
from adaptive_harness.providers.litellm_provider import LiteLLMProvider
from adaptive_harness.profiles import ProfileRegistry
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore
from adaptive_harness.skills import SkillRegistry
from adaptive_harness.tools.builtin import register_builtin_tools
from adaptive_harness.tools.evidence_tools import register_evidence_tools
from adaptive_harness.tools.state_tools import register_state_tools
from adaptive_harness.v06_config import HarnessConfig

app = v05.app


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _state_store(cfg: HarnessConfig) -> TaskStateStore:
    return TaskStateStore(
        _resolved_path(cfg.harness_root, cfg.long_horizon.state_db),
        done_evidence_strength=cfg.long_horizon.task_done_evidence_strength,
        max_tasks=cfg.long_horizon.max_tasks_per_mission,
    )


def build(config_path: str):
    cfg = HarnessConfig.from_yaml(config_path)
    provider = LiteLLMProvider()
    tools = ToolRegistry()
    register_builtin_tools(tools, cfg.workspace)
    register_evidence_tools(tools, cfg.workspace)

    traces = TraceStore(str(_resolved_path(cfg.harness_root, cfg.trace_db)))
    memory = MemoryStore(str(_resolved_path(cfg.harness_root, cfg.memory_db)))
    skill_registry = SkillRegistry(Path(cfg.harness_root) / "skills")
    profile_registry = ProfileRegistry(Path(cfg.harness_root) / "profiles")

    state_store = None
    context_builder = None
    if cfg.long_horizon.enabled:
        state_store = _state_store(cfg)
        context_builder = MissionContextBuilder()
        register_state_tools(
            tools,
            store=state_store,
            traces=traces,
            context_builder=context_builder,
            writable=True,
            default_context_tokens=cfg.long_horizon.context_budget_tokens,
            fact_evidence_strength=cfg.long_horizon.fact_evidence_strength,
        )

    runtime = AgentRuntime(
        config=cfg,
        provider=provider,
        tools=tools,
        traces=traces,
        memory=memory,
        skills=skill_registry,
        profiles=profile_registry,
    )

    if cfg.team is not None and cfg.team.enabled:
        child_tools = ToolRegistry()
        register_builtin_tools(child_tools, cfg.workspace)
        register_evidence_tools(child_tools, cfg.workspace)
        child_tools.unregister("fs_write")
        child_tools.unregister("sandbox_command")
        if state_store is not None:
            register_state_tools(
                child_tools,
                store=state_store,
                traces=traces,
                context_builder=context_builder,
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
            memory=memory,
            skills=skill_registry,
            profiles=profile_registry,
        )

        cache = None
        if cfg.team.semantic_cache_enabled:
            cache_path = _resolved_path(cfg.harness_root, cfg.team.semantic_cache_db)
            if cfg.team.economy.enabled and cfg.team.economy.multi_space_cache_enabled:
                cache = MultiSpaceSemanticWorkCache(
                    cache_path,
                    calibration_min_samples=cfg.team.economy.cache_calibration_min_samples,
                    precision_target=cfg.team.economy.cache_precision_target,
                    entity_overlap_direct=cfg.team.economy.cache_entity_overlap_direct,
                    procedure_floor_direct=cfg.team.economy.cache_procedure_floor_direct,
                )
            else:
                cache = SemanticWorkCache(cache_path)

        if cfg.team.economy.enabled:
            compute_store = ComputeMarketStore(
                _resolved_path(cfg.harness_root, cfg.team.economy.db),
                evidence_half_life_days=cfg.team.economy.evidence_half_life_days,
            )
            confidence_store = ConfidenceCalibrationStore(
                _resolved_path(cfg.harness_root, cfg.team.economy.confidence_db)
            )
            calibrator = TrajectoryConfidenceCalibrator(
                confidence_store,
                min_empirical_samples=cfg.team.economy.confidence_min_empirical_samples,
            )
            arena = None
            if cfg.team.economy.policy_arena_enabled:
                arena = PolicyArenaStore(
                    _resolved_path(cfg.harness_root, cfg.team.economy.policy_arena_db),
                    config=cfg.team.economy,
                )
            orchestrator = AdaptiveTeamOrchestrator(
                config=cfg,
                provider=provider,
                traces=traces,
                child_runner=child_runtime.run,
                cache=cache,
                compute_store=compute_store,
                confidence_calibrator=calibrator,
                policy_arena=arena,
            )
        else:
            orchestrator = TeamOrchestrator(
                config=cfg,
                provider=provider,
                traces=traces,
                child_runner=child_runtime.run,
                cache=cache,
            )
        register_team_orchestration(tools, orchestrator)
    return cfg, provider, traces, runtime


legacy_cli.build = build


@app.command("mission-create")
def mission_create(
    goal: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    external_key: str | None = typer.Option(None, "--external-key"),
):
    """Create/recover durable canonical state for a long-horizon mission."""
    cfg = HarnessConfig.from_yaml(config)
    if not cfg.long_horizon.enabled:
        raise typer.BadParameter("long_horizon is disabled")
    snap = _state_store(cfg).create_mission(goal=goal, external_key=external_key)
    print(snap.model_dump_json(indent=2))


@app.command("mission-status")
def mission_status(
    mission_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    budget_tokens: int | None = typer.Option(None, "--budget-tokens"),
    focus: str = typer.Option("", "--focus"),
):
    """Show canonical mission state plus the token-budgeted slice agents should consume."""
    cfg = HarnessConfig.from_yaml(config)
    store = _state_store(cfg)
    snap = store.snapshot(mission_id)
    budget = budget_tokens or cfg.long_horizon.context_budget_tokens
    packed = MissionContextBuilder().build(snap, focus=focus, max_tokens=budget)
    print(
        json.dumps(
            {
                "mission": snap.model_dump(mode="json"),
                "ready_tasks": [task.id for task in store.ready_tasks(mission_id)],
                "context": packed.model_dump(mode="json"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


@app.command("mission-audit")
def mission_audit(
    mission_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Audit evidence/dependency/revision invariants for a durable mission."""
    cfg = HarnessConfig.from_yaml(config)
    issues = _state_store(cfg).audit(mission_id)
    if not issues:
        print("[green]Mission state audit passed.[/green]")
        return
    print(json.dumps([issue.model_dump(mode="json") for issue in issues], indent=2))
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
