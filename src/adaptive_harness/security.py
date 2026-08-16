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

# Narrower set that the self-improvement pipeline itself may not promote. The shell
# can evolve, but authorization, durable ledgers, promotion, sandbox/tool boundaries,
# provider credential boundary, and the trusted exam remain outside self-promotion.
IMMUTABLE_FROM_SELF_EVOLUTION = (
    "tests/",
    "evals/",
    "config/",
    "src/adaptive_harness/security.py",
    "src/adaptive_harness/contracts.py",
    "src/adaptive_harness/config.py",
    "src/adaptive_harness/cli.py",
    "src/adaptive_harness/channels/",
    "src/adaptive_harness/providers/",
    "src/adaptive_harness/runtime/policy.py",
    "src/adaptive_harness/runtime/trace_store.py",
    "src/adaptive_harness/runtime/tool_registry.py",
    "src/adaptive_harness/tools/builtin.py",
    "src/adaptive_harness/tools/mcp_http.py",
    "src/adaptive_harness/improvement/governance.py",
    "src/adaptive_harness/improvement/patch_security.py",
    "src/adaptive_harness/improvement/evaluator.py",
)
