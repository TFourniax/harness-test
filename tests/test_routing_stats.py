from pathlib import Path

from adaptive_harness.config import HarnessConfig, ModelRole, TeamConfig
from adaptive_harness.orchestration.contracts import WorkItem
from adaptive_harness.orchestration.router import CostAwareModelRouter
from adaptive_harness.orchestration.routing_stats import RoutingStatsStore


def test_router_learns_to_avoid_cheap_model_for_failing_task_family(tmp_path: Path):
    stats = RoutingStatsStore(tmp_path / "routing.sqlite3")
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap-model"),
        team=TeamConfig(routing_min_samples=4, routing_target_success=0.78),
    )
    task = WorkItem(id="x", task="hard code review", profile="code", difficulty=0.4)
    for _ in range(4):
        stats.record(
            model="cheap-model",
            role="cheap",
            profile="code",
            difficulty=0.4,
            succeeded=False,
            cost_usd=0.001,
        )
    decision = CostAwareModelRouter(cfg, stats).for_worker(task)
    assert decision.name == "primary"
    assert "below target" in decision.reason


def test_router_can_keep_cheap_model_when_empirically_reliable(tmp_path: Path):
    stats = RoutingStatsStore(tmp_path / "routing.sqlite3")
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap-model"),
        team=TeamConfig(routing_min_samples=4, routing_target_success=0.70),
    )
    task = WorkItem(id="x", task="classification", profile="generic", difficulty=0.8)
    for _ in range(8):
        stats.record(
            model="cheap-model",
            role="cheap",
            profile="generic",
            difficulty=0.8,
            succeeded=True,
            cost_usd=0.001,
        )
    decision = CostAwareModelRouter(cfg, stats).for_worker(task)
    assert decision.name == "cheap"
    assert "empirical" in decision.reason
