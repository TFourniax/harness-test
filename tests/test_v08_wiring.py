from pathlib import Path

import yaml

from adaptive_harness.security import IMMUTABLE_FROM_SELF_EVOLUTION
from adaptive_harness.v07_cli import build as build_v07
from adaptive_harness.v08_cli import build as build_v08


def _config(tmp_path: Path, *, diversity_enabled: bool = True) -> Path:
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
                "allowed_scopes": ["fs:read", "fs:write", "net:read"],
                "team": {"enabled": False},
                "long_horizon": {
                    "enabled": True,
                    "state_db": ".harness/task-state.sqlite3",
                    "context_budget_tokens": 512,
                },
                "distributed_reasoning": {
                    "enabled": True,
                    "control_db": ".harness/distributed.sqlite3",
                    "max_depth": 1,
                    "max_cells": 4,
                    "max_leaf_attempts": 3,
                },
                "marginal_diversity": {
                    "enabled": diversity_enabled,
                    "db": ".harness/diversity.sqlite3",
                    "max_panel_attempts": 3,
                    "min_samples": 4,
                    "min_utility": 0.05,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_v07_keeps_single_leaf_behavior_when_no_v08_runtime_is_selected(tmp_path: Path):
    _cfg, _provider, _traces, runtime = build_v07(str(_config(tmp_path)))
    assert runtime._hierarchical_service.panel_leaf_runner is None
    assert runtime._mission_dispatcher.panel_leaf_runner is None
    assert not hasattr(runtime, "_panel_leaf_runner")


def test_v08_injects_one_shared_panel_policy_into_hierarchy_and_mission_dispatch(tmp_path: Path):
    cfg, _provider, _traces, runtime = build_v08(str(_config(tmp_path)))
    panel = runtime._panel_leaf_runner
    assert runtime._hierarchical_service.panel_leaf_runner is panel
    assert runtime._mission_dispatcher.panel_leaf_runner is panel
    assert runtime._marginal_diversity_market.store is runtime._marginal_diversity_store
    assert cfg.marginal_diversity.max_panel_attempts == 3


def test_v08_can_disable_panel_without_disabling_v07_cells_or_missions(tmp_path: Path):
    _cfg, _provider, _traces, runtime = build_v08(
        str(_config(tmp_path, diversity_enabled=False))
    )
    assert runtime._hierarchical_service is not None
    assert runtime._mission_dispatcher is not None
    assert runtime._hierarchical_service.panel_leaf_runner is None
    assert runtime._mission_dispatcher.panel_leaf_runner is None
    assert not hasattr(runtime, "_marginal_diversity_market")


def test_v08_policy_and_panel_are_inside_immutable_self_evolution_boundary():
    protected = set(IMMUTABLE_FROM_SELF_EVOLUTION)
    assert "src/adaptive_harness/v08_cli.py" in protected
    assert "src/adaptive_harness/v08_config.py" in protected
    assert "src/adaptive_harness/orchestration/diversity_market.py" in protected
    assert "src/adaptive_harness/orchestration/panel_service.py" in protected
