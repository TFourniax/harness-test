from __future__ import annotations

from pathlib import Path

from adaptive_harness import cli as legacy_cli
from adaptive_harness.config import HarnessConfig
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.orchestration.adaptive_team import AdaptiveTeamOrchestrator
from adaptive_harness.orchestration.multispace_cache import MultiSpaceSemanticWorkCache
from adaptive_harness.orchestration.team import TeamOrchestrator, register_team_orchestration
from adaptive_harness.orchestration.vector_cache import SemanticWorkCache
from adaptive_harness.providers.litellm_provider import LiteLLMProvider
from adaptive_harness.profiles import ProfileRegistry
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore
from adaptive_harness.skills import SkillRegistry
from adaptive_harness.tools.builtin import register_builtin_tools


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def build(config_path: str):
    """Build the v0.4 runtime while preserving the existing command surface."""

    cfg = HarnessConfig.from_yaml(config_path)
    provider = LiteLLMProvider()
    tools = ToolRegistry()
    register_builtin_tools(tools, cfg.workspace)
    traces = TraceStore(str(_resolved_path(cfg.harness_root, cfg.trace_db)))
    memory = MemoryStore(str(_resolved_path(cfg.harness_root, cfg.memory_db)))
    skill_registry = SkillRegistry(Path(cfg.harness_root) / "skills")
    profile_registry = ProfileRegistry(Path(cfg.harness_root) / "profiles")
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
        child_tools.unregister("fs_write")
        child_tools.unregister("sandbox_command")
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

        orchestrator_cls = (
            AdaptiveTeamOrchestrator if cfg.team.economy.enabled else TeamOrchestrator
        )
        register_team_orchestration(
            tools,
            orchestrator_cls(
                config=cfg,
                provider=provider,
                traces=traces,
                child_runner=child_runtime.run,
                cache=cache,
            ),
        )
    return cfg, provider, traces, runtime


# Existing Typer command functions resolve their module-global ``build`` at call time.
# Replacing it here upgrades all current CLI/Telegram commands without duplicating them.
legacy_cli.build = build
app = legacy_cli.app
