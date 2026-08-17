from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class ModelRole(BaseModel):
    model: str
    temperature: float = 0.1
    max_tokens: int = 4096
    tool_mode: Literal["auto", "native", "text"] = "auto"
    fallbacks: list[str] = Field(default_factory=list)
    timeout: float = 90.0
    num_retries: int = 2


class ComputeEconomyConfig(BaseModel):
    """Local adaptive-compute controller. All price/quality hints are cold-start priors only."""

    enabled: bool = True
    db: str = ".harness/compute-economy.sqlite3"
    min_strategy_samples: int = 4  # retained for v0.4 DB compatibility
    min_strategy_evidence_mass: float = 2.0
    evidence_half_life_days: float = 45.0
    exploration_rate: float = 0.06

    # Transparent cold-start priors. Real provider-reported costs replace these per bucket.
    cold_start_cheap_call_usd: float = 0.002
    cold_start_primary_call_usd: float = 0.020

    cost_weight: float = 3.5
    budget_pressure_weight: float = 0.35
    min_expected_gain: float = 0.035
    stop_confidence_floor: float = 0.82

    normal_success_target: float = 0.78
    critical_success_target: float = 0.92
    critical_reliability_penalty: float = 2.4

    cheap_pair_min_difficulty: float = 0.48
    primary_preferred_difficulty: float = 0.82
    critical_mixed_pair_difficulty: float = 0.78
    cheap_pair_diversity_multiplier: float = 1.10
    mixed_pair_diversity_multiplier: float = 1.16

    # v0.5 evidence-grounded stopping/learning.
    confidence_db: str = ".harness/confidence-calibration.sqlite3"
    confidence_min_empirical_samples: int = 12
    direct_cache_min_evidence_strength: float = 0.72

    # Multi-space cache remains fully local by default.
    multi_space_cache_enabled: bool = True
    cache_calibration_min_samples: int = 8
    cache_precision_target: float = 0.995
    cache_entity_overlap_direct: float = 0.90
    cache_procedure_floor_direct: float = 0.90

    # Champion/challenger arena. It may recommend a policy but cannot promote it by itself.
    policy_arena_enabled: bool = True
    policy_arena_db: str = ".harness/compute-policies.sqlite3"
    policy_min_matched_trials: int = 12
    policy_evidence_floor: float = 0.60
    policy_quality_regression_tolerance: float = 0.01
    policy_quality_gain_target: float = 0.02
    policy_cost_reduction_target: float = 0.10
    policy_cost_tolerance: float = 0.05


class TeamConfig(BaseModel):
    enabled: bool = True
    max_agents: int = 6
    max_rounds: int = 3
    max_tasks_per_plan: int = 8
    worker_max_steps: int = 18
    orchestrator_role: Literal["cheap", "primary"] = "cheap"
    synthesizer_role: Literal["cheap", "primary"] = "primary"
    cheap_max_difficulty: float = 0.62
    routing_stats_db: str = ".harness/routing-stats.sqlite3"
    routing_min_samples: int = 4
    routing_target_success: float = 0.78
    utility_floor: float = 0.18
    target_confidence: float = 0.86
    max_report_chars: int = 5000
    semantic_cache_enabled: bool = True
    semantic_cache_db: str = ".harness/team-cache.sqlite3"
    semantic_direct_threshold: float = 0.985
    semantic_reference_threshold: float = 0.90
    stable_cache_ttl_seconds: int = 604800
    volatile_cache_ttl_seconds: int = 600
    economy: ComputeEconomyConfig = Field(default_factory=ComputeEconomyConfig)


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    token_env: str = "TELEGRAM_BOT_TOKEN"
    allowed_user_ids: set[int] = Field(default_factory=set)
    allowed_chat_ids: set[int] = Field(default_factory=set)
    allow_group_chats: bool = False
    poll_timeout_seconds: int = 30
    max_steps: int = 40
    max_cost_usd: float | None = None
    profile: str | None = None


class HarnessConfig(BaseModel):
    primary: ModelRole
    planner: ModelRole | None = None
    verifier: ModelRole | None = None
    critic: ModelRole | None = None
    cheap: ModelRole | None = None
    allowed_scopes: set[str] = Field(default_factory=lambda: {"fs:read", "net:read"})
    approval_risks: set[str] = Field(
        default_factory=lambda: {"external_side_effect", "privileged"}
    )
    harness_root: str = "."
    workspace: str = "."
    trace_db: str = ".harness/traces.sqlite3"
    memory_db: str = ".harness/memory.sqlite3"
    max_parallel_agents: int = 4
    max_run_cost_usd: float | None = None
    evidence_gate_enabled: bool = True
    blind_verification_enabled: bool = True
    auto_maintenance_enabled: bool = False
    maintenance_scan_runs: int = 50
    maintenance_out: str = ".harness/proposals"
    telegram: TelegramChannelConfig | None = None
    team: TeamConfig | None = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))
