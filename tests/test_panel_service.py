from pathlib import Path

import pytest

from adaptive_harness.contracts import Observation, RunStatus, TrustLevel
from adaptive_harness.orchestration.cell_runtime import (
    CellLimits,
    CellPlan,
    CellSpec,
    HierarchicalCellRuntime,
)
from adaptive_harness.orchestration.distributed_control import DistributedControlStore
from adaptive_harness.orchestration.diversity_market import (
    MarginalDecision,
    MarginalDiversityMarket,
    MarginalDiversityStore,
)
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.v07_config import HarnessConfig


def _cfg(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig.model_validate(
        {
            "primary": {"model": "primary/test"},
            "cheap": {"model": "cheap/test"},
            "harness_root": str(tmp_path),
            "workspace": str(tmp_path),
            "team": {
                "enabled": True,
                "worker_max_steps": 8,
                "economy": {
                    "cold_start_cheap_call_usd": 0.001,
                    "cold_start_primary_call_usd": 0.002,
                },
            },
        }
    )


def _obs(call_id: str, *, passed: bool = True) -> Observation:
    return Observation(
        call_id=call_id,
        tool_name="verify_workspace_command",
        ok=True,
        content="pass" if passed else "fail",
        trust=TrustLevel.TOOL,
        metadata={
            "verification_signal": "deterministic_pass" if passed else "deterministic_fail",
            "verification_kind": "tests",
            "verification_claim": "pytest tests pass",
        },
    )


def _run(answer: str, *, observations=None, cost=0.001, status=RunStatus.SUCCEEDED):
    return RunResult(
        run_id=answer,
        status=status,
        answer=answer,
        observations=observations or [],
        reported_cost_usd=cost,
    )


def _runtime(tmp_path: Path, max_attempts: int):
    return HierarchicalCellRuntime(
        DistributedControlStore(tmp_path / "control.sqlite3"),
        limits=CellLimits(max_depth=0, max_cells=1, max_leaf_attempts=max_attempts),
    )


@pytest.mark.asyncio
async def test_strong_first_deterministic_proof_stops_panel_after_one_attempt(tmp_path: Path):
    calls = []

    async def child(goal):
        calls.append(goal)
        return _run("verified first", observations=[_obs("proof-1")])

    market = MarginalDiversityMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    panel = IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=child, market=market, max_panel_attempts=3
    )
    runtime = _runtime(tmp_path, 3)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    result = await runtime.execute(
        CellSpec(
            id="t",
            task="Run pytest tests and verify the code",
            success_criteria=["pytest tests pass"],
            profile="code",
            difficulty=0.8,
            decomposable=False,
        ),
        owner="panel",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=lambda *_: None,
        panel_leaf_runner=panel,
    )
    assert result.success
    assert result.leaf_attempts == 1
    assert len(calls) == 1
    assert result.answer == "verified first"


@pytest.mark.asyncio
async def test_weak_first_attempt_buys_blind_second_attempt_and_selects_its_proof(tmp_path: Path):
    goals = []
    results = [
        _run("plausible but unverified"),
        _run("minority answer with proof", observations=[_obs("proof-2")]),
    ]

    async def child(goal):
        goals.append(goal)
        return results.pop(0)

    market = MarginalDiversityMarket(
        MarginalDiversityStore(tmp_path / "market.sqlite3"),
        min_utility=0.0,
        cost_weight=0.0,
    )
    panel = IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=child, market=market, max_panel_attempts=2
    )
    runtime = _runtime(tmp_path, 2)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    result = await runtime.execute(
        CellSpec(
            id="t",
            task="Run pytest tests and investigate code failures",
            success_criteria=["pytest tests pass"],
            profile="code",
            difficulty=0.75,
            decomposable=False,
        ),
        owner="panel",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=lambda *_: None,
        panel_leaf_runner=panel,
    )
    assert result.success
    assert result.leaf_attempts == 2
    assert result.answer == "minority answer with proof"
    assert "METHOD LANE: postcondition" in goals[0].text
    assert "METHOD LANE: falsification" in goals[1].text
    assert "plausible but unverified" not in goals[1].text


class AlwaysBuyMarket(MarginalDiversityMarket):
    def decide(self, **kwargs):
        return MarginalDecision(
            buy=True,
            slot_index=kwargs["slot_index"],
            expected_gain=0.5,
            expected_independence=0.8,
            expected_cost_usd=kwargs["expected_cost_usd"],
            utility=1.0,
            reason="test override",
        )


@pytest.mark.asyncio
async def test_material_refutation_in_second_branch_blocks_optimistic_panel_success(tmp_path: Path):
    results = [
        # Supporting local observation, but not a strongly task-bound deterministic proof.
        _run(
            "looks good",
            observations=[
                Observation(
                    call_id="support",
                    tool_name="fs_read",
                    ok=True,
                    content="inspection",
                    trust=TrustLevel.TOOL,
                )
            ],
        ),
        _run("tests actually fail", observations=[_obs("refute", passed=False)]),
    ]

    async def child(goal):
        return results.pop(0)

    market = AlwaysBuyMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    panel = IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=child, market=market, max_panel_attempts=2
    )
    runtime = _runtime(tmp_path, 2)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    result = await runtime.execute(
        CellSpec(
            id="t",
            task="Run pytest tests and validate the fix",
            success_criteria=["pytest tests pass"],
            profile="code",
            difficulty=0.8,
            decomposable=False,
        ),
        owner="panel",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=lambda *_: None,
        panel_leaf_runner=panel,
    )
    assert not result.success
    assert result.leaf_attempts == 2
    # The evidence-first selector may prefer the supported branch to the refuted branch, but the
    # combined deterministic evidence still prevents the cell from reporting success.
    assert result.answer == "looks good"


@pytest.mark.asyncio
async def test_global_attempt_ceiling_prevents_panel_from_buying_hidden_second_rollout(tmp_path: Path):
    calls = 0

    async def child(goal):
        nonlocal calls
        calls += 1
        return _run("weak")

    market = AlwaysBuyMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    panel = IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=child, market=market, max_panel_attempts=3
    )
    runtime = _runtime(tmp_path, 1)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    result = await runtime.execute(
        CellSpec(id="t", task="Hard analysis", difficulty=0.8, decomposable=False),
        owner="panel",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=lambda *_: None,
        panel_leaf_runner=panel,
    )
    assert calls == 1
    assert result.leaf_attempts == 1


@pytest.mark.asyncio
async def test_second_attempt_records_measured_marginal_verification_gain(tmp_path: Path):
    results = [
        _run("weak"),
        _run("verified", observations=[_obs("proof")]),
    ]

    async def child(goal):
        return results.pop(0)

    store = MarginalDiversityStore(tmp_path / "market.sqlite3")
    market = MarginalDiversityMarket(store, min_utility=0.0, cost_weight=0.0)
    panel = IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=child, market=market, max_panel_attempts=2
    )
    runtime = _runtime(tmp_path, 2)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    await runtime.execute(
        CellSpec(
            id="t",
            task="Run pytest tests for code",
            success_criteria=["pytest tests pass"],
            profile="code",
            difficulty=0.8,
            decomposable=False,
        ),
        owner="panel",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=lambda *_: None,
        panel_leaf_runner=panel,
    )
    stats = store.stats(market.bucket("code", 0.8, False), 2)
    assert stats is not None
    assert stats.samples == 1
    assert stats.evidence_gain > 0.5
    assert stats.coverage_gain > 0.5
