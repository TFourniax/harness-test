import asyncio
from pathlib import Path

import pytest

from adaptive_harness.orchestration.cell_runtime import (
    CellLimits,
    CellPlan,
    CellSpec,
    CellStatus,
    HierarchicalCellRuntime,
    LeafOutcome,
)
from adaptive_harness.orchestration.distributed_control import (
    BudgetExceeded,
    DistributedControlStore,
)


def _runtime(tmp_path: Path, **limits) -> HierarchicalCellRuntime:
    return HierarchicalCellRuntime(
        DistributedControlStore(tmp_path / "control.sqlite3"),
        limits=CellLimits(**limits),
    )


@pytest.mark.asyncio
async def test_two_child_cells_run_as_two_leaf_attempts_not_three(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=2, max_cells=4, max_leaf_attempts=2)

    async def planner(spec, depth, max_children, allowance):
        if spec.id == "root":
            return CellPlan(
                children=[
                    CellSpec(id="a", task="A", decomposable=False),
                    CellSpec(id="b", task="B", decomposable=False),
                ]
            )
        return CellPlan()

    async def leaf(spec, allowance):
        return LeafOutcome(
            answer=spec.id,
            success=True,
            cost_usd=min(0.01, allowance),
            confidence=0.8,
            evidence_refs=[f"e:{spec.id}"],
        )

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.1,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.success
    assert result.status == CellStatus.COMPLETE
    assert result.cells_opened == 3
    assert result.leaf_attempts == 2
    assert result.cost_usd == pytest.approx(0.02)
    assert result.evidence_refs == ["e:a", "e:b"]


@pytest.mark.asyncio
async def test_children_are_scheduled_concurrently(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=1, max_cells=3, max_leaf_attempts=2)
    both_started = asyncio.Event()
    started: set[str] = set()
    lock = asyncio.Lock()

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(
            children=[
                CellSpec(id="a", task="A", decomposable=False),
                CellSpec(id="b", task="B", decomposable=False),
            ]
        )

    async def leaf(spec, allowance):
        async with lock:
            started.add(spec.id)
            if len(started) == 2:
                both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        return LeafOutcome(answer=spec.id, success=True, cost_usd=0.0)

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.success
    assert result.status == CellStatus.COMPLETE
    assert started == {"a", "b"}


@pytest.mark.asyncio
async def test_single_child_plan_falls_back_to_parent_leaf(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=2, max_cells=4, max_leaf_attempts=1)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(children=[CellSpec(id="lonely", task="not useful")])

    seen = []

    async def leaf(spec, allowance):
        seen.append(spec.id)
        return LeafOutcome(answer="direct", success=True, cost_usd=0.0)

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.1,
        planner=planner,
        leaf_runner=leaf,
    )
    assert seen == ["root"]
    assert result.status == CellStatus.COMPLETE
    assert result.leaf_attempts == 1
    assert result.cells_opened == 1


@pytest.mark.asyncio
async def test_planner_cost_is_charged_before_child_budget_split(tmp_path: Path):
    runtime = _runtime(
        tmp_path,
        max_depth=1,
        max_cells=3,
        max_leaf_attempts=2,
        min_child_budget_usd=0.01,
    )

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(
            planner_cost_usd=0.02,
            children=[
                CellSpec(id="a", task="A", decomposable=False),
                CellSpec(id="b", task="B", decomposable=False),
            ],
        )

    allowances = []

    async def leaf(spec, allowance):
        allowances.append(allowance)
        return LeafOutcome(answer=spec.id, success=True, cost_usd=allowance)

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.10,
        planner=planner,
        leaf_runner=leaf,
    )
    assert sorted(allowances) == pytest.approx([0.04, 0.04])
    assert result.cost_usd == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_reported_leaf_spend_cannot_cross_escrow(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=0, max_cells=1, max_leaf_attempts=1)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def leaf(spec, allowance):
        return LeafOutcome(answer="oops", success=True, cost_usd=allowance + 0.001)

    with pytest.raises(BudgetExceeded):
        await runtime.execute(
            CellSpec(id="root", task="root", decomposable=False),
            owner="lead",
            budget_usd=0.01,
            planner=planner,
            leaf_runner=leaf,
        )


@pytest.mark.asyncio
async def test_leaf_attempt_ceiling_blocks_incomplete_scheduled_work(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=1, max_cells=4, max_leaf_attempts=1)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(
            children=[
                CellSpec(id="a", task="A", decomposable=False),
                CellSpec(id="b", task="B", decomposable=False),
            ]
        )

    async def leaf(spec, allowance):
        await asyncio.sleep(0)
        return LeafOutcome(answer=spec.id, success=True)

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert result.leaf_attempts == 1
    assert not result.success
    assert result.status == CellStatus.BLOCKED
    assert result.blocked_reasons
    assert "CellLimitExceeded" in result.answer


@pytest.mark.asyncio
async def test_analytical_child_failure_is_partial_not_complete(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=1, max_cells=3, max_leaf_attempts=2)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan(
            children=[
                CellSpec(id="a", task="A", decomposable=False),
                CellSpec(id="b", task="B", decomposable=False),
            ]
        )

    async def leaf(spec, allowance):
        return LeafOutcome(
            answer=spec.id,
            success=spec.id == "a",
            confidence=0.8 if spec.id == "a" else 0.2,
        )

    result = await runtime.execute(
        CellSpec(id="root", task="root"),
        owner="lead",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=leaf,
    )
    assert not result.success
    assert result.status == CellStatus.PARTIAL
    assert len(result.children) == 2


@pytest.mark.asyncio
async def test_panel_claims_only_actual_model_attempts(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=0, max_cells=1, max_leaf_attempts=3)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def normal_leaf(spec, allowance):
        raise AssertionError("panel path should replace normal leaf")

    async def panel(spec, allowance, attempts):
        assert await attempts.claim()
        return LeafOutcome(
            answer="strong first answer",
            success=True,
            evidence_refs=["proof-1"],
            attempt_count=99,
        )

    result = await runtime.execute(
        CellSpec(id="root", task="root", decomposable=False),
        owner="lead",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=normal_leaf,
        panel_leaf_runner=panel,
    )
    assert result.success
    assert result.leaf_attempts == 1
    assert "actual_attempts=1" in result.reason


@pytest.mark.asyncio
async def test_panel_cannot_hide_extra_model_calls_from_shared_attempt_budget(tmp_path: Path):
    runtime = _runtime(tmp_path, max_depth=0, max_cells=1, max_leaf_attempts=2)

    async def planner(spec, depth, max_children, allowance):
        return CellPlan()

    async def normal_leaf(spec, allowance):
        raise AssertionError("panel path should replace normal leaf")

    async def panel(spec, allowance, attempts):
        assert await attempts.claim()
        assert await attempts.claim()
        assert not await attempts.claim()
        return LeafOutcome(answer="two attempts", success=True, attempt_count=1)

    result = await runtime.execute(
        CellSpec(id="root", task="root", decomposable=False),
        owner="lead",
        budget_usd=0.05,
        planner=planner,
        leaf_runner=normal_leaf,
        panel_leaf_runner=panel,
    )
    assert result.leaf_attempts == 2
    assert "actual_attempts=2" in result.reason
