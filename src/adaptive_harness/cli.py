from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich import print

from adaptive_harness.channels.base import AgentChannelGateway
from adaptive_harness.channels.telegram import TelegramAccessPolicy, run_telegram_channel
from adaptive_harness.config import HarnessConfig
from adaptive_harness.contracts import Goal, ImprovementProposal, SkillProposal
from adaptive_harness.improvement.engine import SelfImprovementEngine
from adaptive_harness.improvement.evaluator import PatchEvaluator
from adaptive_harness.improvement.governance import PromotionGate
from adaptive_harness.improvement.skill_evolution import SkillEvolutionEngine, SkillPromotionGate
from adaptive_harness.memory.store import MemoryStore
from adaptive_harness.providers.litellm_provider import LiteLLMProvider
from adaptive_harness.profiles import ProfileRegistry
from adaptive_harness.runtime.agent import AgentRuntime
from adaptive_harness.runtime.tool_registry import ToolRegistry
from adaptive_harness.runtime.trace_store import TraceStore
from adaptive_harness.skills import SkillRegistry
from adaptive_harness.tools.builtin import register_builtin_tools

app = typer.Typer(no_args_is_help=True)


def build(config_path: str):
    cfg = HarnessConfig.from_yaml(config_path)
    provider = LiteLLMProvider()
    tools = ToolRegistry()
    register_builtin_tools(tools, cfg.workspace)
    traces = TraceStore(str(_resolved_path(cfg.harness_root, cfg.trace_db)))
    memory = MemoryStore(str(_resolved_path(cfg.harness_root, cfg.memory_db)))
    runtime = AgentRuntime(
        config=cfg,
        provider=provider,
        tools=tools,
        traces=traces,
        memory=memory,
        skills=SkillRegistry(Path(cfg.harness_root) / "skills"),
        profiles=ProfileRegistry(Path(cfg.harness_root) / "profiles"),
    )
    return cfg, provider, traces, runtime


def _resolved_path(root: str, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else Path(root) / path


def _maybe_auto_maintain(cfg, provider, traces) -> Path | None:
    if not cfg.auto_maintenance_enabled:
        return None
    run_ids = traces.recent_run_ids(cfg.maintenance_scan_runs)
    if len(run_ids) < 2:
        return None
    evaluator = PatchEvaluator(cfg.harness_root, [["python", "-m", "pytest", "-q"]])
    engine = SelfImprovementEngine(
        provider=provider,
        role=(cfg.critic or cfg.primary),
        trace_store=traces,
        evaluator=evaluator,
    )
    population_events = []
    for run_id in run_ids:
        population_events.extend(
            {**event, "run_id": run_id} for event in traces.events(run_id)
        )
    weaknesses = engine.miner.mine_population(population_events)
    if not weaknesses:
        return None

    out = _resolved_path(cfg.harness_root, cfg.maintenance_out)
    out.mkdir(parents=True, exist_ok=True)
    for existing in out.glob("*.json"):
        try:
            proposal = ImprovementProposal.model_validate_json(existing.read_text(encoding="utf-8"))
        except Exception:
            continue
        if proposal.weakness == weaknesses[0] and proposal.human_status in {"pending", "approved"}:
            return None

    proposal = asyncio.run(engine.propose_from_runs(run_ids))
    if not proposal:
        return None
    path = out / f"{proposal.id}.json"
    path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    return path


def _apply_promoted_proposal(cfg: HarnessConfig, proposal: ImprovementProposal) -> None:
    ok, reason = PromotionGate.promotable(proposal)
    if not ok:
        raise ValueError(f"not promotable: {reason}")
    harness_root = Path(cfg.harness_root).resolve()
    if not (harness_root / ".git").exists():
        raise ValueError("harness_root is not a Git checkout; initialize/clone Git before promotion")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=harness_root,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if dirty:
        raise ValueError("tracked harness worktree is dirty; commit/stash before promotion")
    check = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=harness_root,
        input=proposal.patch,
        text=True,
        capture_output=True,
    )
    if check.returncode:
        raise ValueError(check.stderr or "patch no longer applies")
    subprocess.run(
        ["git", "apply", "-"],
        cwd=harness_root,
        input=proposal.patch,
        text=True,
        check=True,
    )


def _interactive_review_maintenance(cfg: HarnessConfig, proposal_path: Path) -> None:
    proposal = ImprovementProposal.model_validate_json(
        proposal_path.read_text(encoding="utf-8")
    )
    print("[cyan]Recurring weakness detected. Tested self-improvement candidate:[/cyan]")
    print(f"[bold]title[/bold]: {proposal.title}")
    print(f"[bold]weakness[/bold]: {proposal.weakness}")
    print(f"[bold]targets[/bold]: {', '.join(proposal.target_files) or '(none)'}")
    print(
        f"[bold]gates[/bold]: regression={proposal.regression_passed} "
        f"security={proposal.security_passed}"
    )
    print(f"[bold]fingerprint[/bold]: {PromotionGate.fingerprint(proposal)}")
    print("[bold]exact diff[/bold]:")
    print(proposal.patch or "(empty patch)")
    approved = typer.confirm(
        "Approve this exact tested change for application to the harness checkout?",
        default=False,
    )
    proposal.human_status = "approved" if approved else "rejected"
    proposal_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    if not approved:
        print("[yellow]Proposal rejected; live harness unchanged.[/yellow]")
        return
    try:
        _apply_promoted_proposal(cfg, proposal)
    except Exception as exc:
        print(
            "[yellow]Human approval recorded, but automatic application was blocked:[/yellow] "
            f"{exc}"
        )
        print(
            "Use promote-proposal after the Git/worktree precondition is fixed; the approved "
            "fingerprint remains recorded."
        )
        return
    print(
        "[green]Approved patch applied to the harness checkout.[/green] "
        "It is intentionally not committed or pushed; restart the harness after reviewing git diff."
    )


def _print_run_result(result) -> None:
    print(f"[bold]run_id[/bold]: {result.run_id}")
    print(f"[bold]status[/bold]: {result.status.value}")
    if result.approval_id:
        print(f"[yellow]approval required[/yellow]: {result.approval_id}")
    if result.reported_cost_usd:
        print(f"[bold]reported model cost[/bold]: ${result.reported_cost_usd:.6f}")
    if result.answer:
        print(result.answer)


@app.command()
def chat(
    session: str = typer.Option("default", "--session", "-s"),
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    max_steps: int = typer.Option(40),
    max_cost_usd: float | None = typer.Option(None, "--max-cost-usd"),
):
    """Interactive direct conversation with durable session context and exact action approvals."""
    cfg, provider, traces, runtime = build(config)
    print(f"[bold]Adaptive Agent Harness[/bold] session={session}. Type /exit to quit.")
    while True:
        try:
            text = typer.prompt("you")
        except (EOFError, KeyboardInterrupt):
            print("bye")
            break
        if text.strip().lower() in {"/exit", "/quit"}:
            break
        if not text.strip():
            continue
        result = asyncio.run(
            runtime.run(
                Goal(
                    text=text,
                    session_id=session,
                    max_steps=max_steps,
                    max_cost_usd=max_cost_usd,
                )
            )
        )
        while result.approval_id:
            approval = traces.get_approval(result.approval_id)
            if approval is None:
                break
            print("[yellow]High-impact action awaiting exact human approval[/yellow]")
            print(json.dumps({
                "summary": approval.summary,
                "payload": approval.payload,
                "fingerprint": approval.fingerprint,
            }, indent=2, default=str))
            approved = typer.confirm("Approve this exact action?", default=False)
            traces.set_approval(approval.id, "approved" if approved else "rejected")
            result = asyncio.run(runtime.resume(result.run_id, approval.id))
        _print_run_result(result)
        proposal_path = _maybe_auto_maintain(cfg, provider, traces)
        if proposal_path:
            print(
                f"[cyan]Auto-maintenance produced a proposal artifact:[/cyan] {proposal_path}"
            )
            _interactive_review_maintenance(cfg, proposal_path)


@app.command()
def run(
    task: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    max_steps: int = typer.Option(40),
    max_cost_usd: float | None = typer.Option(None, "--max-cost-usd"),
    profile: str | None = typer.Option(None, "--profile"),
):
    """Run one governed agent task."""
    cfg, provider, traces, runtime = build(config)
    result = asyncio.run(
        runtime.run(
            Goal(
                text=task,
                max_steps=max_steps,
                max_cost_usd=max_cost_usd,
                profile=profile,
            )
        )
    )
    _print_run_result(result)
    proposal_path = _maybe_auto_maintain(cfg, provider, traces)
    if proposal_path:
        print(
            f"[cyan]Auto-maintenance proposal awaiting human review:[/cyan] {proposal_path}"
        )


@app.command()
def improve(
    run_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    out: str = typer.Option(".harness/proposals", "--out"),
):
    """Mine a run for recurring weaknesses and generate a tested minimal harness patch."""
    cfg, provider, traces, _ = build(config)
    evaluator = PatchEvaluator(cfg.harness_root, [["python", "-m", "pytest", "-q"]])
    engine = SelfImprovementEngine(
        provider=provider,
        role=(cfg.critic or cfg.primary),
        trace_store=traces,
        evaluator=evaluator,
    )
    proposal = asyncio.run(engine.propose_from_run(run_id))
    if not proposal:
        print("No evidence-backed improvement proposal found.")
        raise typer.Exit(0)
    dest = _resolved_path(cfg.harness_root, out)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{proposal.id}.json"
    path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Proposal: {path}")
    print(f"regression_passed={proposal.regression_passed} security_passed={proposal.security_passed}")


@app.command()
def maintain(
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    scan_runs: int = typer.Option(50, "--scan-runs", min=1, max=1000),
    out: str = typer.Option(".harness/proposals", "--out"),
):
    """Mine recent runs for recurring weakness clusters and propose one tested repair."""
    cfg, provider, traces, _ = build(config)
    run_ids = traces.recent_run_ids(scan_runs)
    if not run_ids:
        print("No traces available for maintenance.")
        raise typer.Exit(0)
    evaluator = PatchEvaluator(cfg.harness_root, [["python", "-m", "pytest", "-q"]])
    engine = SelfImprovementEngine(
        provider=provider,
        role=(cfg.critic or cfg.primary),
        trace_store=traces,
        evaluator=evaluator,
    )
    proposal = asyncio.run(engine.propose_from_runs(run_ids))
    if not proposal:
        print(f"No recurring evidence-backed weakness found across {len(run_ids)} recent runs.")
        raise typer.Exit(0)
    dest = _resolved_path(cfg.harness_root, out)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{proposal.id}.json"
    path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Maintenance proposal: {path}")
    print(f"regression_passed={proposal.regression_passed} security_passed={proposal.security_passed}")


@app.command("learn-skill")
def learn_skill(
    run_ids: list[str] = typer.Argument(...),
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    out: str = typer.Option(".harness/skill-proposals", "--out"),
):
    """Distill a reusable skill candidate from one or more successful runs."""
    cfg, provider, traces, _ = build(config)
    engine = SkillEvolutionEngine(provider, (cfg.critic or cfg.primary), traces)
    proposal = asyncio.run(engine.propose(run_ids))
    if not proposal:
        print("No reusable skill candidate found in the selected successful traces.")
        raise typer.Exit(0)
    dest = _resolved_path(cfg.harness_root, out)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{proposal.id}.json"
    path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Skill proposal: {path}")


@app.command("approve-skill")
def approve_skill(path: str):
    p = Path(path)
    proposal = SkillProposal.model_validate_json(p.read_text(encoding="utf-8"))
    proposal.human_status = "approved"
    p.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Approved skill candidate {proposal.name}")


@app.command("promote-skill")
def promote_skill(
    path: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
    skills_root: str | None = typer.Option(None, "--skills-root"),
):
    cfg = HarnessConfig.from_yaml(config)
    p = Path(path)
    proposal = SkillProposal.model_validate_json(p.read_text(encoding="utf-8"))
    root = (
        Path(skills_root)
        if skills_root is not None and Path(skills_root).is_absolute()
        else _resolved_path(cfg.harness_root, skills_root or "skills")
    )
    dest = SkillPromotionGate.promote(proposal, str(root))
    print(f"Activated skill: {dest}")


@app.command("approve-proposal")
def approve_proposal(path: str):
    """Record explicit human approval for an already-evaluated proposal."""
    p = Path(path)
    proposal = ImprovementProposal.model_validate_json(p.read_text(encoding="utf-8"))
    proposal.human_status = "approved"
    p.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Approved proposal {proposal.id}; patch fingerprint={PromotionGate.fingerprint(proposal)}")


@app.command("reject-proposal")
def reject_proposal(path: str):
    p = Path(path)
    proposal = ImprovementProposal.model_validate_json(p.read_text(encoding="utf-8"))
    proposal.human_status = "rejected"
    p.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"Rejected proposal {proposal.id}")


@app.command("promote-proposal")
def promote_proposal(
    path: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Apply an approved, regression-passing proposal to the harness source checkout."""
    cfg = HarnessConfig.from_yaml(config)
    p = Path(path)
    proposal = ImprovementProposal.model_validate_json(p.read_text(encoding="utf-8"))
    try:
        _apply_promoted_proposal(cfg, proposal)
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    print(
        f"Promoted proposal {proposal.id}. Review git diff, then commit through your normal process."
    )


@app.command("approve-action")
def approve_action(
    approval_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Approve one exact pending high-impact tool call by fingerprint."""
    _, _, traces, _ = build(config)
    traces.set_approval(approval_id, "approved")
    approval = traces.get_approval(approval_id)
    print(f"Approved action {approval_id}; fingerprint={approval.fingerprint if approval else 'unknown'}")


@app.command("reject-action")
def reject_action(
    approval_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Reject one pending high-impact tool call."""
    _, _, traces, _ = build(config)
    traces.set_approval(approval_id, "rejected")
    print(f"Rejected action {approval_id}")


@app.command()
def resume(
    run_id: str,
    approval_id: str,
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Resume a durable run after an action approval/rejection decision."""
    _, _, _, runtime = build(config)
    result = asyncio.run(runtime.resume(run_id, approval_id))
    print(f"[bold]run_id[/bold]: {result.run_id}")
    print(f"[bold]status[/bold]: {result.status.value}")
    if result.approval_id:
        print(f"[yellow]approval required[/yellow]: {result.approval_id}")
    if result.answer:
        print(result.answer)


@app.command("telegram")
def telegram_channel(
    config: str = typer.Option("config/harness.yaml", "--config", "-c"),
):
    """Run the allowlisted Telegram channel using durable long polling."""
    cfg, _, traces, runtime = build(config)
    telegram = cfg.telegram
    if telegram is None or not telegram.enabled:
        raise typer.BadParameter("telegram.enabled must be true in the harness config")
    if not telegram.allowed_user_ids:
        raise typer.BadParameter(
            "telegram.allowed_user_ids must contain at least one explicit Telegram user ID"
        )
    token = os.getenv(telegram.token_env, "").strip()
    if not token:
        raise typer.BadParameter(
            f"missing Telegram bot token in environment variable {telegram.token_env}"
        )

    gateway = AgentChannelGateway(
        runtime=runtime,
        traces=traces,
        max_steps=telegram.max_steps,
        max_cost_usd=telegram.max_cost_usd,
        profile=telegram.profile,
    )
    access = TelegramAccessPolicy(
        allowed_user_ids=frozenset(telegram.allowed_user_ids),
        allowed_chat_ids=frozenset(telegram.allowed_chat_ids),
        allow_group_chats=telegram.allow_group_chats,
    )
    print(
        "[bold]Telegram channel[/bold] running with default-deny access; "
        f"authorized_users={len(telegram.allowed_user_ids)}"
    )
    try:
        asyncio.run(
            run_telegram_channel(
                token=token,
                gateway=gateway,
                traces=traces,
                access=access,
                poll_timeout_seconds=telegram.poll_timeout_seconds,
            )
        )
    except KeyboardInterrupt:
        print("Telegram channel stopped.")


@app.command()
def doctor(config: str = typer.Option("config/harness.yaml", "--config", "-c")):
    cfg = HarnessConfig.from_yaml(config)
    harness_root = Path(cfg.harness_root).resolve()
    workspace = Path(cfg.workspace).resolve()
    profile_registry = ProfileRegistry(harness_root / "profiles")
    checks = {
        "harness_root_exists": harness_root.exists(),
        "workspace_exists": workspace.exists(),
        "profiles_loaded": sorted(profile_registry.load()),
        "git_available": shutil.which("git") is not None,
        "docker_available": shutil.which("docker") is not None,
        "runtime_self_write_protection": (
            "full_harness_control_plane"
            if (workspace / "pyproject.toml").exists()
            and 'name = "adaptive-agent-harness"' in (workspace / "pyproject.toml").read_text(encoding="utf-8", errors="ignore")
            else "workspace_harness_state_only"
        ),
    }
    print(json.dumps(checks, indent=2))
