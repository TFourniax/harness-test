from __future__ import annotations

from dataclasses import dataclass

from adaptive_harness.config import HarnessConfig, ModelRole
from adaptive_harness.orchestration.contracts import WorkItem
from adaptive_harness.orchestration.routing_stats import RoutingStatsStore


@dataclass(frozen=True)
class RouteDecision:
    name: str
    role: ModelRole
    reason: str


class CostAwareModelRouter:
    """Progressive model escalation with empirical task-family routing memory."""

    def __init__(self, config: HarnessConfig, stats: RoutingStatsStore | None = None) -> None:
        self.config = config
        self.stats = stats

    def for_worker(self, task: WorkItem, *, prior_failed: bool = False) -> RouteDecision:
        team = self.config.team
        if team is None:
            return RouteDecision("primary", self.config.primary, "team config absent")
        cheap = self.config.cheap
        if cheap is None or prior_failed or task.critical:
            return RouteDecision(
                "primary",
                self.config.primary,
                "critical task, cheap route unavailable, or previous cheap attempt failed",
            )

        if self.stats is not None:
            stats = self.stats.get(
                model=cheap.model,
                role="cheap",
                profile=task.profile or "generic",
                difficulty=task.difficulty,
            )
            if stats and stats.samples >= team.routing_min_samples:
                if stats.success_rate >= team.routing_target_success:
                    return RouteDecision(
                        "cheap",
                        cheap,
                        f"empirical cheap success={stats.success_rate:.2f} over {stats.samples} samples",
                    )
                return RouteDecision(
                    "primary",
                    self.config.primary,
                    f"empirical cheap success={stats.success_rate:.2f} below target {team.routing_target_success:.2f}",
                )

        if task.difficulty <= team.cheap_max_difficulty:
            return RouteDecision(
                "cheap",
                cheap,
                f"cold-start difficulty {task.difficulty:.2f} <= cheap threshold {team.cheap_max_difficulty:.2f}",
            )
        return RouteDecision(
            "primary",
            self.config.primary,
            "cold-start high-difficulty task",
        )

    def record_worker(self, task: WorkItem, decision: RouteDecision, *, succeeded: bool, cost_usd: float) -> None:
        if self.stats is None:
            return
        self.stats.record(
            model=decision.role.model,
            role=decision.name,
            profile=task.profile or "generic",
            difficulty=task.difficulty,
            succeeded=succeeded,
            cost_usd=cost_usd,
        )

    def orchestrator(self) -> RouteDecision:
        team = self.config.team
        if team and team.orchestrator_role == "cheap" and self.config.cheap is not None:
            return RouteDecision("cheap", self.config.cheap, "cheap orchestrator configured")
        return RouteDecision("primary", self.config.primary, "primary orchestrator")

    def synthesizer(self) -> RouteDecision:
        team = self.config.team
        if team and team.synthesizer_role == "cheap" and self.config.cheap is not None:
            return RouteDecision("cheap", self.config.cheap, "cheap synthesizer configured")
        return RouteDecision("primary", self.config.primary, "primary synthesizer")
