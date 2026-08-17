from pathlib import Path

import pytest

from adaptive_harness.orchestration.cell_runtime import CellSpec
from adaptive_harness.orchestration.diversity_market import (
    MarginalDiversityMarket,
    MarginalDiversityStore,
)
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.v08_config import HarnessConfig, MarginalDiversityConfig


def _cfg(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig.model_validate(
        {
            "primary": {"model": "primary/test"},
            "cheap": {"model": "cheap/test"},
            "harness_root": str(tmp_path),
            "workspace": str(tmp_path),
            "team": {
                "enabled": True,
                "economy": {
                    "primary_preferred_difficulty": 0.82,
                    "cold_start_cheap_call_usd": 0.002,
                    "cold_start_primary_call_usd": 0.020,
                },
            },
        }
    )


def _panel(tmp_path: Path):
    async def unused_child(goal):
        raise AssertionError("this test only inspects routing policy")

    market = MarginalDiversityMarket(MarginalDiversityStore(tmp_path / "market.sqlite3"))
    return IndependenceAwarePanelRunner(
        config=_cfg(tmp_path), child_runner=unused_child, market=market
    )


def test_v08_default_cold_start_utility_is_conservative():
    assert MarginalDiversityConfig().min_utility == pytest.approx(0.120)


def test_hard_panel_alternates_primary_then_cheap_then_primary(tmp_path: Path):
    panel = _panel(tmp_path)
    spec = CellSpec(id="hard", task="hard", profile="code", difficulty=0.9)
    assert [panel._role_for(spec, slot) for slot in (1, 2, 3)] == [
        "primary",
        "cheap",
        "primary",
    ]


def test_critical_panel_also_uses_cheap_falsifier_as_second_lane(tmp_path: Path):
    panel = _panel(tmp_path)
    spec = CellSpec(
        id="critical", task="critical", profile="research", difficulty=0.6, critical=True
    )
    assert [panel._role_for(spec, slot) for slot in (1, 2, 3)] == [
        "primary",
        "cheap",
        "primary",
    ]


def test_unaffordable_primary_degrades_to_cheap_when_cheap_fits(tmp_path: Path):
    panel = _panel(tmp_path)
    assert panel._affordable_role("primary", 0.003) == "cheap"


def test_no_model_call_is_purchased_when_even_cheap_prior_cannot_fit(tmp_path: Path):
    panel = _panel(tmp_path)
    assert panel._affordable_role("primary", 0.001) is None
    assert panel._affordable_role("cheap", 0.001) is None
