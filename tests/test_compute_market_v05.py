from pathlib import Path

from adaptive_harness.config import HarnessConfig, ModelRole, TeamConfig
from adaptive_harness.orchestration.compute_market import ComputeAction, ComputeEconomyStore, ComputeMarket
from adaptive_harness.orchestration.contracts import WorkItem


def _market(tmp_path: Path):
    cfg = HarnessConfig(
        primary=ModelRole(model="primary"),
        cheap=ModelRole(model="cheap"),
        harness_root=str(tmp_path),
        workspace=str(tmp_path),
        team=TeamConfig(enabled=True),
    )
    store = ComputeEconomyStore(tmp_path / "economy.sqlite3")
    return ComputeMarket(cfg, store), store


def test_weak_model_only_failures_do_not_immediately_poison_strategy_prior(tmp_path: Path):
    market, store = _market(tmp_path)
    task = WorkItem(id="x", task="simple classification", difficulty=0.2)
    bid = market.quote(ComputeAction.CHEAP_SINGLE, task)
    for _ in range(12):
        market.record(
            bid, task, succeeded=False, cost_usd=0.001, quality_gain=0.0,
            evidence_strength=0.05, outcome_score=0.0,
        )
    stats = store.get(ComputeAction.CHEAP_SINGLE, task)
    assert stats is not None
    assert stats.evidence_mass < market.economy.min_strategy_evidence_mass
    assert market.choose(task).action == ComputeAction.CHEAP_SINGLE


def test_strong_evidence_can_overturn_cheap_cold_start(tmp_path: Path):
    market, store = _market(tmp_path)
    task = WorkItem(id="x", task="simple classification", difficulty=0.2)
    bid = market.quote(ComputeAction.CHEAP_SINGLE, task)
    for _ in range(6):
        market.record(
            bid, task, succeeded=False, cost_usd=0.001, quality_gain=0.0,
            evidence_strength=1.0, outcome_score=0.0,
        )
    stats = store.get(ComputeAction.CHEAP_SINGLE, task)
    assert stats is not None and stats.evidence_mass >= 6
    assert market.choose(task).action != ComputeAction.CHEAP_SINGLE


def test_hard_budget_stops_when_no_compute_action_is_affordable(tmp_path: Path):
    market, _ = _market(tmp_path)
    task = WorkItem(
        id="critical-budget",
        task="verify critical irreversible operation",
        difficulty=0.95,
        critical=True,
    )
    bid = market.choose(task, remaining_budget_usd=0.0001)
    assert bid.action == ComputeAction.STOP
    assert "hard remaining budget" in bid.reason
