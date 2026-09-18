from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich import print

from adaptive_harness import cli as legacy_cli
from adaptive_harness import v07_cli as v07
from adaptive_harness.orchestration.diversity_market import (
    MarginalDiversityMarket,
    MarginalDiversityStore,
)
from adaptive_harness.contracts import TraceEvent
from adaptive_harness.orchestration.jev import JevDecisionEngine
from adaptive_harness.orchestration.panel_service import IndependenceAwarePanelRunner
from adaptive_harness.v08_config import HarnessConfig

app = v07.app


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def build(config_path: str):
    cfg = HarnessConfig.from_yaml(config_path)
    _base_cfg, provider, traces, runtime = v07.build(config_path)
    runtime.config = cfg

    if cfg.marginal_diversity.enabled:
        service = getattr(runtime, "_hierarchical_service", None)
        dispatcher = getattr(runtime, "_mission_dispatcher", None)
        if service is not None:
            engine = None
            if cfg.jev.mode != "off":
                def audit(event):
                    traces.append(TraceEvent(
                        run_id="jev:" + (event["fingerprint"] or "local"),
                        kind="jev_decision", payload=event,
                    ))
                engine = JevDecisionEngine(cfg.jev, audit=audit)
                service.decision_engine = engine if cfg.jev.planner_gate else None
                runtime._jev_decision_engine = engine
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
                decision_engine=engine if cfg.jev.panel_routing else None,
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


@app.command("jev-status")
def jev_status(
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Show safe local configuration and key presence; never contacts TypeSafe."""
    cfg = HarnessConfig.from_yaml(config)
    print(json.dumps({
        **cfg.jev.model_dump(mode="json"),
        "key_present": bool(os.environ.get(cfg.jev.api_key_env, "").strip()),
        "live_validation": "not checked by this command",
    }, indent=2))


if __name__ == "__main__":
    app()
