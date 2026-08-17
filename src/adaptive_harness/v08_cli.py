from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print

from adaptive_harness import cli as legacy_cli
from adaptive_harness import v07_cli as v07
from adaptive_harness.orchestration.diversity_market import (
    MarginalDiversityMarket,
    MarginalDiversityStore,
)
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.v08_config import HarnessConfig

app = v07.app


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def build(config_path: str):
    _base_cfg, provider, traces, runtime = v07.build(config_path)
    cfg = HarnessConfig.from_yaml(config_path)
    runtime.config = cfg

    if cfg.marginal_diversity.enabled:
        service = getattr(runtime, "_hierarchical_service", None)
        dispatcher = getattr(runtime, "_mission_dispatcher", None)
        if service is not None:
            dcfg = cfg.marginal_diversity
            store = MarginalDiversityStore(
                _resolved_path(cfg.harness_root, dcfg.db)
            )
            market = MarginalDiversityMarket(
                store,
                min_samples=dcfg.min_samples,
                min_utility=dcfg.min_utility,
                cost_weight=dcfg.cost_weight,
                redundancy_floor=dcfg.redundancy_floor,
            )
            panel = IndependenceAwarePanelRunner(
                config=cfg,
                child_runner=service.child_runner,
                market=market,
                max_panel_attempts=dcfg.max_panel_attempts,
            )
            # v0.7 objects intentionally expose optional panel slots. The exact same hierarchy,
            # leases, escrows and mission semantics are reused; only leaf rollout purchasing changes.
            service.panel_leaf_runner = panel
            if dispatcher is not None:
                dispatcher.panel_leaf_runner = panel
            runtime._marginal_diversity_store = store
            runtime._marginal_diversity_market = market
            runtime._panel_leaf_runner = panel

    return cfg, provider, traces, runtime


legacy_cli.build = build


@app.command("diversity-status")
def diversity_status(
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Show v0.8 marginal-diversity policy configuration."""
    cfg = HarnessConfig.from_yaml(config)
    print(json.dumps(cfg.marginal_diversity.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    app()
