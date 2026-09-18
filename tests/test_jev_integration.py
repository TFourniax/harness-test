"""Exercise real harness decision sites with a mock JEV endpoint; no live performance claim."""
import json
import sqlite3

import httpx
import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from adaptive_harness.contracts import ModelTurn
from adaptive_harness.orchestration.cell_runtime import CellSpec, LeafAttemptBudget
from adaptive_harness.orchestration.diversity_market import (
    MarginalDiversityMarket, MarginalDiversityStore, MarginalDecision,
)
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION
from adaptive_harness.v08_cli import build, app
from adaptive_harness.v08_config import HarnessConfig
from test_hierarchical_service import _service, FakeProvider
from test_panel_service import _cfg, _run, _runtime, _obs
from test_v08_wiring import _config
from test_jev import config, engine, response_for, decide


@pytest.fixture(autouse=True)
def isolated_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "integration-test-not-a-real-key")


def spec(**updates):
    return CellSpec(**{"id": "test", "task": "Run pytest tests and verify code",
                       "success_criteria": ["pytest tests pass"], "profile": "code",
                       "difficulty": 0.75, **updates})


@pytest.mark.asyncio
async def test_confident_direct_route_skips_actual_generative_planner_call(tmp_path):
    provider = FakeProvider([])
    service = _service(tmp_path, provider)
    service.decision_engine, requests = engine(chosen={"structure": "direct_leaf"})
    plan = await service.planner(spec(), 0, 4, 0.2)
    assert provider.calls == 0 and len(requests) == 1
    assert plan.children == [] and "planner skipped" in plan.rationale
    assert plan.planner_cost_usd == pytest.approx(0.0000042)
    # Returning an empty plan does NOT say that the task is verified or complete.
    assert not hasattr(plan, "success")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["decompose", "shadow", "timeout", "missing_key", "critical"])
async def test_original_planner_fallback_and_decision_cost_are_preserved(tmp_path, monkeypatch, case):
    provider = FakeProvider([ModelTurn(content='{"children":[]}', usage={"cost_usd": 0.002})])
    service = _service(tmp_path, provider)
    async def timeout(_): raise httpx.ReadTimeout("synthetic")
    service.decision_engine, requests = engine(
        cfg=config(mode="shadow" if case == "shadow" else "active"),
        chosen={"structure": "decompose" if case == "decompose" else "direct_leaf"},
        handler=timeout if case == "timeout" else None,
    )
    if case == "missing_key": monkeypatch.delenv("TYPESAFE_API_KEY")
    plan = await service.planner(spec(critical=case == "critical"), 0, 4, 0.2)
    assert provider.calls == 1 and plan.children == []
    assert plan.planner_cost_usd == pytest.approx(0.002 + service.decision_engine.accounted_usd)
    assert len(requests) == (0 if case in ("critical", "missing_key") else 1)


@pytest.mark.asyncio
async def test_planner_failure_does_not_erase_paid_routing_cost(tmp_path):
    service = _service(tmp_path, FakeProvider([]))  # pops from empty list after a routing request
    service.decision_engine, _ = engine(chosen={"structure": "decompose"})
    plan = await service.planner(spec(), 0, 4, 0.2)
    assert plan.planner_cost_usd > 0 and "Planner failed" in plan.rationale


@pytest.mark.asyncio
async def test_hierarchy_settles_decision_cost_with_real_leaf_cost(tmp_path):
    service = _service(tmp_path, FakeProvider([]))
    service.decision_engine, _ = engine(chosen={"structure": "direct_leaf"})
    async def child(_): return _run("completed leaf", cost=0.001)
    service.child_runner = child
    outcome = await service.cells.execute(spec(), owner="test", budget_usd=0.2,
                                          planner=service.planner, leaf_runner=service.leaf_runner)
    assert outcome.cost_usd == pytest.approx(0.0010042) and outcome.leaf_attempts == 1


def panel(tmp_path, e, *, max_attempts=3, market=None, results=None):
    goals = []
    results = list(results or [_run("weak") for _ in range(max_attempts)])
    async def child(goal):
        goals.append(goal)
        return results.pop(0)
    market = market or MarginalDiversityMarket(
        MarginalDiversityStore(tmp_path / "market.sqlite3"), min_utility=0, cost_weight=0,
    )
    runner = IndependenceAwarePanelRunner(config=_cfg(tmp_path), child_runner=child,
                                          market=market, max_panel_attempts=max_attempts,
                                          decision_engine=e)
    return runner, goals


@pytest.mark.asyncio
async def test_panel_uses_fixed_local_labels_accounts_cost_and_keeps_answers_blind(tmp_path):
    e, requests = engine(chosen={"role": "primary", "method": "invariants"})
    runner, goals = panel(tmp_path, e)
    outcome = await runner(spec(), 0.1, LeafAttemptBudget(_runtime(tmp_path, 3)))
    assert len(goals) == 3 and all(g.model_role == "primary" for g in goals)
    assert "METHOD LANE: postcondition" in goals[0].text
    assert "METHOD LANE: invariants" in goals[1].text
    assert "METHOD LANE: falsification" in goals[2].text
    assert outcome.cost_usd == pytest.approx(0.003 + e.accounted_usd)
    assert all("weak" not in req.content.decode() for req in requests)
    assert all("weak" not in goal.text for goal in goals)


@pytest.mark.asyncio
async def test_critical_panel_keeps_alternating_tiers_and_never_pays_jev(tmp_path):
    e, requests = engine(chosen={"role": "cheap"})
    runner, goals = panel(tmp_path, e)
    await runner(spec(critical=True), 0.1, LeafAttemptBudget(_runtime(tmp_path, 3)))
    assert [g.model_role for g in goals] == ["primary", "cheap", "primary"]
    assert not requests


@pytest.mark.asyncio
async def test_hard_first_attempt_cannot_be_downgraded(tmp_path):
    e, requests = engine(chosen={"role": "cheap"})
    runner, goals = panel(tmp_path, e, max_attempts=1)
    await runner(spec(difficulty=0.95), 0.1, LeafAttemptBudget(_runtime(tmp_path, 1)))
    assert goals[0].model_role == "primary" and not requests


@pytest.mark.asyncio
async def test_no_decision_purchase_when_no_leaf_slots_remain(tmp_path):
    e, requests = engine()
    runner, goals = panel(tmp_path, e)
    budget = LeafAttemptBudget(_runtime(tmp_path, 1))
    assert await budget.claim()
    outcome = await runner(spec(), 0.1, budget)
    assert outcome.cost_usd == 0 and not goals and not requests


@pytest.mark.asyncio
async def test_deterministic_proof_stops_further_paid_decisions(tmp_path):
    e, requests = engine()
    runner, goals = panel(tmp_path, e, results=[_run("proved", observations=[_obs("proof")])])
    outcome = await runner(spec(), 0.1, LeafAttemptBudget(_runtime(tmp_path, 3)))
    assert outcome.attempt_count == 1 and len(requests) == 1 and len(goals) == 1


@pytest.mark.asyncio
async def test_tight_budget_uses_local_route_instead_of_spending_on_jev(tmp_path):
    e, requests = engine()
    runner, goals = panel(tmp_path, e, max_attempts=1)
    outcome = await runner(spec(), 0.001, LeafAttemptBudget(_runtime(tmp_path, 1)))
    assert not requests and len(goals) == 1 and outcome.cost_usd == 0.001


@pytest.mark.asyncio
async def test_market_rechecks_costlier_proposal_and_can_veto_it(tmp_path):
    class CheapOnlyMarket(MarginalDiversityMarket):
        def decide(self, **kw):
            return MarginalDecision(buy=kw["expected_cost_usd"] < 0.002,
                slot_index=kw["slot_index"], expected_gain=0.5, expected_independence=0.8,
                expected_cost_usd=kw["expected_cost_usd"], utility=1, reason="cheap only")
    e, requests = engine(chosen={"role": "primary"})
    market = CheapOnlyMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    runner, goals = panel(tmp_path, e, market=market)
    outcome = await runner(spec(), 0.1, LeafAttemptBudget(_runtime(tmp_path, 3)))
    assert len(goals) == 1 and len(requests) == 2
    assert outcome.cost_usd == pytest.approx(0.001 + e.accounted_usd)


@pytest.mark.asyncio
async def test_jev_usage_overrun_without_worker_is_still_returned(tmp_path):
    e, requests = engine(mutate=lambda raw: raw["usage"].update(input_tokens=1_000_000))
    runner, goals = panel(tmp_path, e, max_attempts=1)
    outcome = await runner(spec(), 0.02, LeafAttemptBudget(_runtime(tmp_path, 1)))
    assert not goals and len(requests) == 1
    assert outcome.cost_usd == 0.042 and outcome.attempt_count == 0
    assert e.circuit_open and not outcome.success


@pytest.mark.asyncio
async def test_runtime_wires_shared_engine_into_hierarchy_and_dispatch_panel_with_audit(tmp_path):
    path = _config(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["jev"] = {"mode": "shadow", "allow_remote_state": True, "panel_routing": True}
    path.write_text(yaml.safe_dump(raw))
    _, _, traces, runtime = build(str(path))
    e = runtime._jev_decision_engine
    assert e is runtime._hierarchical_service.decision_engine
    assert e is runtime._mission_dispatcher.panel_leaf_runner.decision_engine
    e.transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json=response_for(json.loads(request.content))))
    await decide(e)
    with sqlite3.connect(traces.path) as db:
        row = db.execute("SELECT payload FROM events WHERE kind='jev_decision'").fetchone()
    assert row and "PRIVATE-TASK" not in row[0]
    assert json.loads(row[0])["reason"] == "shadow"


def test_default_wiring_leaves_free_panel_routing_alone(tmp_path):
    path = _config(tmp_path)
    raw = yaml.safe_load(path.read_text())
    raw["jev"] = {"mode": "active"}
    path.write_text(yaml.safe_dump(raw))
    _, _, _, runtime = build(str(path))
    assert runtime._hierarchical_service.decision_engine is runtime._jev_decision_engine
    assert runtime._panel_leaf_runner.decision_engine is None


@pytest.mark.parametrize("disabled", ["distributed_reasoning", "marginal_diversity", "both_sites"])
def test_invalid_jev_wiring_fails_at_configuration_time(tmp_path, disabled):
    raw = yaml.safe_load(_config(tmp_path).read_text())
    raw["jev"] = {"mode": "active"}
    if disabled == "both_sites": raw["jev"].update(planner_gate=False, panel_routing=False)
    else: raw[disabled]["enabled"] = False
    with pytest.raises(ValidationError): HarnessConfig.model_validate(raw)


def test_cli_status_is_safe_and_new_policy_is_protected(tmp_path):
    output = CliRunner().invoke(app, ["jev-status", "--config", str(_config(tmp_path))])
    assert output.exit_code == 0
    assert '"mode": "off"' in output.stdout
    assert '"key_present": true' in output.stdout
    assert "integration-test-not-a-real-key" not in output.stdout
    assert "src/adaptive_harness/orchestration/jev.py" in IMMUTABLE_FROM_SELF_EVOLUTION


@pytest.mark.asyncio
async def test_lost_rollout_slot_still_charges_decision_before_reporting_blocked(tmp_path):
    from adaptive_harness.orchestration.cell_runtime import CellLimitExceeded
    runtime = _runtime(tmp_path, 1)
    escrow = runtime.control.create_root_escrow(scope="race", owner="test", allocated_usd=0.1)

    async def racing_response(request):
        runtime._counters.leaves = 1  # another concurrent cell claims the last real slot
        return httpx.Response(200, json=response_for(json.loads(request.content)))

    e, _ = engine(handler=racing_response)
    runner, goals = panel(tmp_path, e)
    with pytest.raises(CellLimitExceeded):
        await runtime._execute_leaf(spec(), escrow_id=escrow.escrow_id,
                                    leaf_runner=lambda *_: None, panel_leaf_runner=runner)
    assert not goals
    assert runtime.control.escrow(escrow.escrow_id).spent_usd == pytest.approx(e.accounted_usd)
    assert e.accounted_usd > 0
