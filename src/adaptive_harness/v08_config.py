from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from adaptive_harness.v07_config import HarnessConfig as V07HarnessConfig


class MarginalDiversityConfig(BaseModel):
    """Evidence-first market for buying the second/third real leaf attempt."""

    enabled: bool = True
    db: str = ".harness/marginal-diversity.sqlite3"
    max_panel_attempts: int = Field(default=3, ge=1, le=4)
    min_samples: int = Field(default=5, ge=1, le=1000)
    min_utility: float = Field(default=0.060, ge=0.0, le=1.0)
    cost_weight: float = Field(default=3.0, ge=0.0, le=1000.0)
    redundancy_floor: float = Field(default=0.24, ge=0.0, le=1.0)


class HarnessConfig(V07HarnessConfig):
    marginal_diversity: MarginalDiversityConfig = Field(
        default_factory=MarginalDiversityConfig
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
