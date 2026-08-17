from pathlib import Path

from adaptive_harness.config import HarnessConfig, ModelRole, TeamConfig
from adaptive_harness.orchestration.compute_market import (
    ComputeAction,
    ComputeEconomyStore,
    ComputeMarket,
)
from adaptive_harness.orchestration.contracts import WorkItem


def _market(tmp_path: Path) -> ComputeMarket:
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        harness_root=str(tmp_path),
        workspace=str(tmp_path),
        team=TeamConfig(enabled=True),
    )
    return ComputeMarket(cfg, ComputeEconomyStore(tmp_path / "economy.sqlite3"))


def test_cold_start_allocates_compute_by_difficulty_and_criticality(tmp_path: Path):
    market = _market(tmp_path)

    easy = WorkItem(id="easy", task="classify records", difficulty=0.2)
    medium = WorkItem(id="medium", task="audit several alternatives", difficulty=0.6)
    hard = WorkItem(id="hard", task="design complex architecture", difficulty=0.9)
    critical = WorkItem(
        id="critical",
        task="verify irreversible migration",
        difficulty=0.9,
        critical=True,
    )

    assert market.choose(easy).action == ComputeAction.CHEAP_SINGLE
    assert market.choose(medium).action == ComputeAction.CHEAP_PAIR
    assert market.choose(hard).action == ComputeAction.PRIMARY_SINGLE
    assert market.choose(critical).action == ComputeAction.MIXED_PAIR


def test_empirical_failures_can_overturn_cold_start_strategy(tmp_path: Path):
    market = _market(tmp_path)
    task = WorkItem(id="x", task="analyze recurring technical task", difficulty=0.3)

    cheap_bid = market.quote(ComputeAction.CHEAP_SINGLE, task)
    pair_bid = market.quote(ComputeAction.CHEAP_PAIR, task)
    for _ in range(12):
        market.record(
            cheap_bid,
            task,
            succeeded=False,
            cost_usd=0.001,
            quality_gain=0.0,
        )
        market.record(
            pair_bid,
            task,
            succeeded=False,
            cost_usd=0.002,
            quality_gain=0.0,
        )

    assert market.choose(task).action == ComputeAction.PRIMARY_SINGLE


def test_high_confidence_can_stop_before_spending_more_compute(tmp_path: Path):
    market = _market(tmp_path)
    task = WorkItem(id="x", task="minor optional follow-up", difficulty=0.2)
    bid = market.choose(task, current_confidence=0.99)
    assert bid.action == ComputeAction.STOP
    assert bid.estimated_cost_usd == 0.0


def test_budget_pressure_excludes_estimated_over_budget_actions(tmp_path: Path):
    market = _market(tmp_path)
    task = WorkItem(id="x", task="review small item", difficulty=0.2)
    bid = market.choose(task, remaining_budget_usd=0.003)
    assert bid.action == ComputeAction.CHEAP_SINGLE
    assert bid.estimated_cost_usd <= 0.003
