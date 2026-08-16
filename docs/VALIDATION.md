# Validation Snapshot

Date: 2026-08-16

This file records what was actually checked in the build environment. It is intentionally narrower than a production certification.

## Passed in this build

- `pytest -q`: **46 passed**.
- `python -m compileall -q src`: passed.
- CLI import and command registration: passed.
- `adaptive-harness doctor -c config/harness.example.yaml`: harness/workspace discovered, six task profiles loaded, Git detected, runtime self-write protection detected.
- Text-only model tool protocol: valid tool envelope and invalid-JSON/fail-retry behavior unit-tested.
- Provider/runtime resilience: planner degradation, actor-provider failure, protocol failure, and fail-closed high-impact verifier outage tested with deterministic fake providers.
- Capability policy, exact action approval/resume, non-idempotent execution ledger, evidence gate, profile ceilings, tool-schema validation, and session persistence tested.
- HTTP SSRF defenses: private/local destinations and redirect behavior covered by tests.
- MCP adapter: stateless request metadata, default-deny tool import, local policy mapping, schema fingerprint drift rejection, and external-output trust labeling tested with `httpx.MockTransport`.
- Self-write firewall, candidate patch root-of-trust protection, regression evaluation, promotion gate, recurring weakness mining, and skill-evolution quarantine covered by tests.

## Not live-tested in this build environment

- A real OpenRouter, Replicate, Anthropic, OpenAI-compatible, or other LiteLLM provider call: no provider credentials were supplied, and the build environment did not have LiteLLM preinstalled. `pyproject.toml` declares LiteLLM as a runtime dependency; the gateway/parser/control behavior is unit-tested independently.
- Real Docker sandbox execution: Docker is not available in this build environment. The runtime detects that condition; production should use a stronger microVM/gVisor/Firecracker-class isolation layer for hostile code.
- Production network egress enforcement: application SSRF checks are defense-in-depth, not a substitute for an egress proxy/firewall.
- Real external side-effect adapters such as email, payments, GitHub writes, CRM writes, browsers, or cloud infrastructure. These should be connected through explicit local tool/MCP policies and tested against staging accounts.
- A statistically meaningful cross-domain benchmark proving superiority over every private/public harness. No such universal benchmark or access to all private systems exists. The repository instead provides falsifiable eval and promotion boundaries for measuring future versions on the deployment's actual task distribution.

## Production acceptance condition

Before calling a deployment production-ready, run the same harness/model combinations on held-out task suites for coding, source-grounded research, scraping, operations, advisory work, prompt injection, tool poisoning, long-horizon recovery, cost, and latency. Keep the eval bank outside the candidate's writable boundary and require rollback/canary evidence for promoted self-modifications.

## Channel validation added in v0.2.0

The Telegram/channel layer is covered by local deterministic tests for default-deny user authorization, optional chat allowlisting, private-chat defaults, callback payload size, message chunking, stable session identity, durable update cursor storage, and prevention of cross-session action approvals. No live Telegram token was used in the validation environment, so real Bot API network delivery is intentionally not claimed as tested here.
