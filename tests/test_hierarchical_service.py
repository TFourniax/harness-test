from pathlib import Path

import pytest

from adaptive_harness.contracts import ModelTurn, RunStatus
from adaptive_harness.orchestration.cell_runtime import (
    CellLimits,
    CellSpec,
    HierarchicalCellRuntime,
)
from adaptive_harness.orchestration.distributed_control import DistributedControlStore
from adaptive_harness.orchestration.hierarchical_service import (
    LLMHierarchicalService,
    hierarchy_worthwhile,
)
from adaptive_harness.runtime.agent import RunResult
from adaptive_harness.v07_config import HarnessConfig


class FakeProvider:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return self.turns.pop(0)


def _cfg(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig.model_validate(
        {
            "primary": {"model": "primary/test"},
            "cheap": {"model": "cheap/test"},
            "harness_root": str(tmp_path),
            "workspace": str(tmp_path),
            "team": {"enabled": True, "worker_max_steps": 8},
            "distributed_reasoning": {
                "max_depth": 2,
                "max_cells": 6,
                "max_leaf_attempts": 4,
                "hierarchy_min_difficulty": 0.70,
                "hierarchy_min_goal_chars": 100,
            },
        }
    )


def _service(tmp_path: Path, provider: FakeProvider):
    cfg = _cfg(tmp_path)
    cells = HierarchicalCellRuntime(
        DistributedControlStore(tmp_path / "control.sqlite3"),
        limits=CellLimits(max_depth=2, max_cells=6, max_leaf_attempts=4),
    )

    async def child_runner(goal):
        return RunResult(run_id="child", status=RunStatus.SUCCEEDED, answer="ok")

    return LLMHierarchicalService(
        config=cfg,
        provider=provider,
        child_runner=child_runner,
        cells=cells,
    )


def test_hierarchy_gate_rejects_short_flat_work_even_if_not_trivial():
    assert not hierarchy_worthwhile(
        "Fix this isolated parser bug",
        difficulty=0.8,
        min_difficulty=0.7,
        min_goal_chars=100,
    )


def test_hierarchy_gate_accepts_structured_high_difficulty_work():
    assert hierarchy_worthwhile(
        "Design the end-to-end architecture across the repo and validate multiple systems",
        difficulty=0.85,
        min_difficulty=0.7,
        min_goal_chars=200,
    )


@pytest.mark.asyncio
async def test_planner_deduplicates_redundant_children_and_collapses_to_leaf(tmp_path: Path):
    provider = FakeProvider(
        [
            ModelTurn(
                content='{"rationale":"split","children":['
                '{"task":"Audit auth","difficulty":0.8},'
                '{"task":"  audit   auth  ","difficulty":0.8}]}' ,
                usage={"cost_usd": 0.001},
            )
        ]
    )
    service = _service(tmp_path, provider)
    plan = await service.planner(
        CellSpec(id="root", task="Comprehensive audit architecture", difficulty=0.9),
        depth=0,
        max_children=4,
        allowance_usd=0.05,
    )
    assert plan.children == []
    assert plan.planner_cost_usd == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_planner_assigns_hierarchical_ids_and_preserves_distinct_branches(tmp_path: Path):
    provider = FakeProvider(
        [
            ModelTurn(
                content='{"rationale":"independent","children":['
                '{"task":"Audit auth","difficulty":0.8,"budget_weight":2},'
                '{"task":"Audit persistence","difficulty":0.7,"budget_weight":1}]}' ,
                usage={"cost_usd": 0.002},
            )
        ]
    )
    service = _service(tmp_path, provider)
    plan = await service.planner(
        CellSpec(id="root", task="Comprehensive architecture audit", difficulty=0.9),
        depth=0,
        max_children=4,
        allowance_usd=0.05,
    )
    assert [child.id for child in plan.children] == ["root.1", "root.2"]
    assert [child.task for child in plan.children] == ["Audit auth", "Audit persistence"]
    assert [child.budget_weight for child in plan.children] == pytest.approx([2.0, 1.0])


@pytest.mark.asyncio
async def test_leaf_runner_passes_hard_escrow_budget_to_child_goal(tmp_path: Path):
    cfg = _cfg(tmp_path)
    provider = FakeProvider([])
    cells = HierarchicalCellRuntime(
        DistributedControlStore(tmp_path / "control.sqlite3"),
        limits=CellLimits(max_depth=0, max_cells=1, max_leaf_attempts=1),
    )
    seen = {}

    async def child_runner(goal):
        seen["goal"] = goal
        return RunResult(
            run_id="child",
            status=RunStatus.SUCCEEDED,
            answer="verified",
            reported_cost_usd=0.006,
        )

    service = LLMHierarchicalService(
        config=cfg,
        provider=provider,
        child_runner=child_runner,
        cells=cells,
    )
    outcome = await service.leaf_runner(
        CellSpec(id="leaf", task="inspect", profile="code", difficulty=0.5),
        0.007,
    )
    assert seen["goal"].max_cost_usd == pytest.approx(0.007)
    assert seen["goal"].model_role == "cheap"
    assert outcome.cost_usd == pytest.approx(0.006)
