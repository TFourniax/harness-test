from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from adaptive_harness.config import HarnessConfig as BaseHarnessConfig


class LongHorizonConfig(BaseModel):
    """Durable canonical mission state kept outside model context."""

    enabled: bool = True
    state_db: str = ".harness/task-state.sqlite3"
    context_budget_tokens: int = 3000
    task_done_evidence_strength: float = 0.60
    fact_evidence_strength: float = 0.25
    max_tasks_per_mission: int = 256


class HarnessConfig(BaseHarnessConfig):
    long_horizon: LongHorizonConfig = Field(default_factory=LongHorizonConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
