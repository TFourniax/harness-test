from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from adaptive_harness.contracts import Goal, RiskLevel, ToolSpec


class TaskProfile(BaseModel):
    """Evolvable task-family strategy below the immutable capability ceiling."""

    name: str
    description: str = ""
    match_terms: list[str] = Field(default_factory=list)
    guidance: str = ""
    tool_patterns: list[str] = Field(default_factory=list)
    allowed_risks: set[RiskLevel] = Field(default_factory=lambda: set(RiskLevel))
    actor_role: Literal["primary", "cheap"] = "primary"

    def allows_tool(self, spec: ToolSpec) -> bool:
        if spec.risk not in self.allowed_risks:
            return False
        if not self.tool_patterns:
            return True
        return any(fnmatch.fnmatchcase(spec.name, pattern) for pattern in self.tool_patterns)


class ProfileRegistry:
    """Transparent solve-time router for separately evolvable task-family profiles.

    Routing is deterministic by default so the agent cannot silently reinterpret a task
    merely to gain a broader tool surface. A caller may explicitly set Goal.profile.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, TaskProfile]:
        profiles: dict[str, TaskProfile] = {}
        for path in sorted(self.root.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profile = TaskProfile.model_validate(raw)
            profiles[profile.name] = profile
        if "generic" not in profiles:
            profiles["generic"] = TaskProfile(name="generic", description="Fallback profile")
        return profiles

    def select(self, goal: Goal) -> TaskProfile:
        profiles = self.load()
        if goal.profile:
            if goal.profile not in profiles:
                raise ValueError(f"unknown task profile: {goal.profile}")
            return profiles[goal.profile]

        tokens = set(re.findall(r"[a-zA-ZÀ-ÿ0-9_-]+", goal.text.lower()))
        best: tuple[float, str] | None = None
        for name, profile in profiles.items():
            if name == "generic" or not profile.match_terms:
                continue
            terms = {term.lower() for term in profile.match_terms}
            hits = len(tokens & terms)
            if not hits:
                continue
            score = hits / max(1, len(terms))
            candidate = (score, name)
            if best is None or candidate > best:
                best = candidate
        return profiles[best[1]] if best else profiles["generic"]
