from pathlib import Path

import pytest
import yaml

from adaptive_harness.contracts import RiskLevel
from adaptive_harness.orchestration.cell_runtime import CellSpec
from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION
from adaptive_harness.v07_cli import build


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "harness.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "primary": {"model": "primary/test"},
                "cheap": {"model": "cheap/test"},
                "harness_root": str(tmp_path),
                "workspace": str(tmp_path),
                "trace_db": ".harness/traces.sqlite3",
                "memory_db": ".harness/memory.sqlite3",
                "allowed_scopes": ["fs:read", "fs:write", "net:read", "exec:sandbox"],
                "team": {"enabled": False},
                "long_horizon": {
                    "enabled": True,
                    "state_db": ".harness/task-state.sqlite3",
                    "context_budget_tokens": 512,
                },
                "distributed_reasoning": {
                    "enabled": True,
                    "control_db": ".harness/distributed.sqlite3",
                    "max_depth": 2,
                    "max_cells": 5,
                    "max_leaf_attempts": 3,
                    "hierarchy_min_difficulty": 0.7,
                    "hierarchy_min_goal_chars": 120,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_v07_build_registers_hierarchy_and_revision_bound_dispatch(tmp_path: Path):
    cfg, _provider, _traces, runtime = build(str(_config(tmp_path)))
    hierarchy = runtime.tools.get("hierarchical_orchestrate")
    dispatch = runtime.tools.get("mission_dispatch_next")
    assert hierarchy.risk == RiskLevel.READ
    assert hierarchy.idempotent
    assert dispatch.risk == RiskLevel.REVERSIBLE_WRITE
    assert dispatch.required_scopes == {"fs:write"}
    assert not dispatch.idempotent
    assert "expected_revision" in dispatch.input_schema["required"]
    assert cfg.distributed_reasoning.max_leaf_attempts == 3


@pytest.mark.asyncio
async def test_flat_mission_task_uses_zero_token_planner_gate(tmp_path: Path):
    _cfg, _provider, _traces, runtime = build(str(_config(tmp_path)))
    planner = runtime._mission_planner
    plan = await planner(
        CellSpec(
            id="mission-t1",
            task="Fix this isolated parser bug",
            difficulty=0.85,
            decomposable=True,
        ),
        0,
        4,
        0.05,
    )
    assert plan.children == []
    assert plan.planner_cost_usd == 0.0
    assert "zero-token" in plan.rationale


def test_v07_distributed_control_plane_is_immutable_from_self_evolution():
    protected = set(IMMUTABLE_FROM_SELF_EVOLUTION)
    assert "src/adaptive_harness/v07_cli.py" in protected
    assert "src/adaptive_harness/v07_config.py" in protected
    assert "src/adaptive_harness/orchestration/distributed_control.py" in protected
    assert "src/adaptive_harness/orchestration/cell_runtime.py" in protected
    assert "src/adaptive_harness/orchestration/mission_dispatcher.py" in protected
    assert "src/adaptive_harness/orchestration/hierarchical_service.py" in protected
