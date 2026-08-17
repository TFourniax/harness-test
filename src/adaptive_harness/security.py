from __future__ import annotations

# A normal task-running agent must not edit its own control plane or approval/eval
# state through ordinary workspace tools. Self-improvement goes through the separate
# candidate/evaluation/human-promotion path instead.
HARNESS_RUNTIME_SELF_WRITE_PROTECTED = (
    ".git",
    ".github",
    ".harness",
    "config",
    "evals",
    "profiles",
    "skills",
    "src/adaptive_harness",
    "tests",
    "pyproject.toml",
)

# The self-improvement pipeline may evolve task-solving components, but not the trust,
# authorization, verification, evaluation or compute-policy-promotion boundaries that
# decide what counts as evidence, what work is durably complete, or how hard resource
# ceilings/leases are enforced.
IMMUTABLE_FROM_SELF_EVOLUTION = (
    "tests/",
    "evals/",
    "config/",
    "src/adaptive_harness/security.py",
    "src/adaptive_harness/contracts.py",
    "src/adaptive_harness/config.py",
    "src/adaptive_harness/cli.py",
    "src/adaptive_harness/v05_cli.py",
    "src/adaptive_harness/v06_cli.py",
    "src/adaptive_harness/v07_cli.py",
    "src/adaptive_harness/v07_config.py",
    "src/adaptive_harness/channels/",
    "src/adaptive_harness/providers/",
    "src/adaptive_harness/runtime/policy.py",
    "src/adaptive_harness/runtime/trace_store.py",
    "src/adaptive_harness/runtime/tool_registry.py",
    "src/adaptive_harness/tools/builtin.py",
    "src/adaptive_harness/tools/evidence_tools.py",
    "src/adaptive_harness/tools/state_tools.py",
    "src/adaptive_harness/tools/mcp_http.py",
    "src/adaptive_harness/orchestration/verification.py",
    "src/adaptive_harness/orchestration/confidence.py",
    "src/adaptive_harness/orchestration/compute_market.py",
    "src/adaptive_harness/orchestration/policy_arena.py",
    "src/adaptive_harness/orchestration/policy_lab.py",
    "src/adaptive_harness/orchestration/benchmark.py",
    "src/adaptive_harness/orchestration/state_plane.py",
    "src/adaptive_harness/orchestration/context_budget.py",
    "src/adaptive_harness/orchestration/distributed_control.py",
    "src/adaptive_harness/orchestration/cell_runtime.py",
    "src/adaptive_harness/orchestration/mission_dispatcher.py",
    "src/adaptive_harness/orchestration/hierarchical_service.py",
    "src/adaptive_harness/improvement/governance.py",
    "src/adaptive_harness/improvement/patch_security.py",
    "src/adaptive_harness/improvement/evaluator.py",
)
