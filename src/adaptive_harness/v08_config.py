from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from adaptive_harness.orchestration.jev import JevConfig

from adaptive_harness.v07_config import HarnessConfig as V07HarnessConfig


class MarginalDiversityConfig(BaseModel):
    """Evidence-first market for buying the second/third real leaf attempt."""

    enabled: bool = True
    db: str = ".harness/marginal-diversity.sqlite3"
    max_panel_attempts: int = Field(default=3, ge=1, le=4)
    min_samples: int = Field(default=5, ge=1, le=1000)
    # Cold-start should prefer one good attempt. Extra rollouts need a meaningful expected
    # verification gain; empirical bucket history can later overturn this conservative prior.
    min_utility: float = Field(default=0.120, ge=0.0, le=1.0)
    cost_weight: float = Field(default=3.0, ge=0.0, le=1000.0)
    redundancy_floor: float = Field(default=0.24, ge=0.0, le=1.0)


class HarnessConfig(V07HarnessConfig):
    jev: JevConfig = Field(default_factory=JevConfig)
    marginal_diversity: MarginalDiversityConfig = Field(
        default_factory=MarginalDiversityConfig
    )

    @model_validator(mode="after")
    def check_jev_wiring(self):
        if self.jev.mode != "off" and not (self.jev.planner_gate or self.jev.panel_routing):
            raise ValueError("Enable at least one JEV decision site")
        if self.jev.mode != "off" and not (
            self.distributed_reasoning.enabled and self.marginal_diversity.enabled
        ):
            raise ValueError("JEV routing requires distributed_reasoning and marginal_diversity")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "HarnessConfig":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.model_validate(yaml.safe_load(handle))
