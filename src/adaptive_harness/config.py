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

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as f:
            return cls.model_validate(yaml.safe_load(f))
