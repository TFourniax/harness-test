from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from adaptive_harness.v06_config import HarnessConfig as V06HarnessConfig


class DistributedReasoningConfig(BaseModel):
    """Hard ceilings for recoverable hierarchical reasoning cells."""

    enabled: bool = True
    control_db: str = ".harness/distributed-control.sqlite3"
    lease_ttl_seconds: int = Field(default=300, ge=5, le=86400)

    max_depth: int = Field(default=2, ge=0, le=8)
    max_cells: int = Field(default=8, ge=1, le=128)
    max_leaf_attempts: int = Field(default=6, ge=1, le=128)
    max_children_per_cell: int = Field(default=4, ge=2, le=16)
    min_child_budget_usd: float = Field(default=0.002, ge=0.0)

    default_hierarchy_budget_usd: float = Field(default=0.10, ge=0.0)
    default_dispatch_budget_usd: float = Field(default=0.10, ge=0.0)
    hierarchy_min_difficulty: float = Field(default=0.70, ge=0.0, le=1.0)
    hierarchy_min_goal_chars: int = Field(default=260, ge=0, le=10000)


class HarnessConfig(V06HarnessConfig):
    distributed_reasoning: DistributedReasoningConfig = Field(
        default_factory=DistributedReasoningConfig
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
