# Adaptive Agent Harness

A model-agnostic control plane for long-lived AI agents that can research, scrape, code, use tools, work as a governed team, learn from traces, and improve their own orchestration **without being allowed to silently weaken the live trust boundary**.

The design objective is:

> **maximum task quality for the minimum justified compute, under evidence, bounded capabilities, durable state, independent verification, and human-governed evolution.**

**Current frontier snapshot: v0.5.0 — Evidence-Grounded Compute Economy.** The reference suite currently contains **83 tests**. GitHub Actions verifies source, compilation and critical Ruff checks, builds a deterministic ZIP, extracts it into a clean directory, and executes the full validation suite again before accepting the snapshot.

This is a research/reference implementation, not a claim of universal superiority or production certification. Real provider-backed held-out benchmarks remain the gate for claims about quality, dollar savings, latency or superiority to another harness.

## Architecture

```text
human / Telegram / API
          │
          ▼
   governed parent agent
          │
 capability + approval policy
          │
          ├──────── simple task ─────────► single-agent loop
          │
          ▼
    team_orchestrate
          │
   complexity / value gate
          │
          ▼
      sparse DAG planner
          │
   ┌──────┼────────┐
   ▼      ▼        ▼
 worker  worker   worker       independent branches in parallel
   │      │        │
   └──── trajectory evidence ───┐
                                ▼
                    verification certificates
                                │
                                ▼
                    calibrated team confidence
                                │
                       lead synthesis / audit
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
              sufficient                material gap
                 │                             │
                STOP                  compute market bid
                                               │
                    ┌──────────────────────────┼─────────────────────────┐
                    ▼                          ▼                         ▼
                cached work               cheap/mixed              primary
                    │                       coalition                   │
                    └──────────────────────────┴─────────────────────────┘
                                               │
                                      bounded next round
```

The parent remains the authority. Team outputs are deliberation data, not permissions. Persistent writes and external effects still pass through the normal policy, evidence, verification and exact human-approval path.

## v0.5 — Evidence-Grounded Compute Economy

v0.4 learned from operational success, cost and synthesis-confidence gain. v0.5 makes that learning substantially harder to game: **compute strategies receive meaningful credit primarily when their output is supported by environment-backed evidence.**

### Verification certificates

Each bounded worker result can receive a `VerificationCertificate`:

```text
VERIFIED   strong task-bound deterministic postcondition
SUPPORTED  useful evidence, but not a full proof
UNVERIFIED model result without sufficient environment evidence
REFUTED    task-relevant deterministic postcondition failed
```

A certificate records evidence strength, task-scope coverage, deterministic status, independent source count, evidence references and reasons. It is **not** a capability token and never grants permissions.

A critical anti-reward-hacking rule is that a passing command does not automatically verify a task. The verification engine estimates whether the check is actually a postcondition of the assigned subtask. For example, `compileall` succeeding is useful evidence for syntax/build work, but cannot certify a market-analysis conclusion simply because the check describes itself as validation.

### Evidence tools

The child runtime receives two explicit evidence-producing primitives in addition to normal governed tools:

- `verify_workspace_command`: constrained test/lint/typecheck/compile/build commands in the disposable read-only-host sandbox. Arbitrary shell commands are rejected from this verification lane.
- `source_fetch`: public-source retrieval with host, final URL and content hash metadata. The returned page remains `UNTRUSTED_EXTERNAL`; source diversity is evidence, never authority.

### Trajectory-aware confidence

Raw model confidence is not accepted as calibrated correctness. `TrajectoryConfidenceCalibrator` shrinks confidence toward uncertainty when evidence is weak, penalizes unresolved/failed work, reacts strongly to deterministic refutation and can later blend externally labelled reliability data.

This means a synthesizer saying “0.96 confident” can still trigger one bounded verification follow-up if the trajectory contains little evidence.

### Evidence-weighted strategy learning

The compute market persists strategy statistics by task family/difficulty, but v0.5 adds evidence mass and temporal decay. A model-only success contributes little. Strong verified outcomes contribute much more. Old observations gradually lose influence, allowing the routing policy to adapt when model quality or provider economics change.

The compute market can still choose among:

```text
stop
cheap_single
cheap_pair
primary_single
mixed_pair
```

but a hard remaining-dollar budget is a true ceiling: if no candidate shape fits, the result is `stop`, including for critical work. The economic layer cannot create money, permissions, agent slots or rounds.

## Champion / challenger policy arena

Routing policy evolution is separated from ordinary live learning.

- live and shadow observations may inform diagnostics;
- challenger policies can be evaluated at zero extra decision authority;
- **only paired, sufficiently strong benchmark evidence can make a challenger promotable**;
- promotion is bound to a SHA-256 fingerprint of the exact comparison (policies, trial count, quality, cost, pass rates and recommendation);
- if benchmark evidence changes after review, the old approval fingerprint becomes invalid.

This prevents the compute optimizer from silently promoting itself because a few live traces looked favourable.

The CLI exposes `adaptive-harness economy-status` for champion/challenger visibility. Policy promotion remains human-governed.

## Sparse multi-agent execution

The v0.3/v0.4 foundations remain:

- zero-token complexity gate for obvious simple work;
- sparse dependency DAG instead of broadcast MoA communication;
- independent branches run concurrently;
- context capsules contain only root goal, bounded subtask, relevant constraints/dependencies and optional prior analogue;
- pair strategies consume two actual child slots;
- cheap-worker loop depth scales with difficulty;
- synthesis can reopen only targeted material gaps;
- children cannot perform normal persistent host writes or external side effects;
- code workers can test/build inside an ephemeral Docker copy while the host workspace stays read-only.

## Multi-space semantic work cache

The local cache separates:

```text
intent      what is being attempted
procedure   audit / inspect / validate / repair / research / ...
entities    paths, URLs, IDs, versions and other material targets
full        complete normalized work text
```

Direct semantic reuse requires the normal freshness/workspace/provenance gates **plus sufficient verification evidence**. Weak legacy or model-only results are reference hints, not semantic truth. Negative reuse feedback can tighten thresholds.

The default backend is intentionally **SQLite + deterministic local feature hashing**. No Qdrant, pgvector, embedding API or additional service is required. Richer retrieval backends can be plugged in later without becoming capability authorities.

## Trust boundary

The following principles remain non-negotiable:

- external/privileged actions require the configured exact human approval;
- approval is bound to the exact action fingerprint;
- high-impact actions are independently checked before approval and again before execution;
- remote MCP metadata is not authorization; import is local-policy/default-deny with schema review/pinning;
- external content is untrusted data;
- profiles may reduce capability but cannot expand it;
- non-idempotent execution uses a durable ledger;
- the ordinary actor cannot rewrite harness/config/evals/skills/Git metadata while operating on the harness checkout;
- self-improvement candidates cannot modify their trusted exam or promotion boundary;
- v0.5 additionally protects verification, confidence calibration, compute-market and policy-arena components from automatic self-promotion.

## Model / provider layer

The runtime uses LiteLLM normalization and is not tied to one model vendor. Role model IDs can point at OpenRouter, Replicate/OpenAI-compatible endpoints, Anthropic, Gemini, Ollama or other LiteLLM-supported providers. Roles include primary, planner, verifier, critic and cheap worker, with per-role fallback/retry/timeout/tool-mode settings.

## Human channels

The channel layer is transport-neutral. Telegram is the first adapter and remains default-deny:

- allowlisted users;
- private chats by default;
- durable per-human/per-conversation sessions;
- exact action approval buttons;
- cross-session approval protection;
- bot token read only from an environment variable.

Channels are transports around the governed runtime, never tool bypasses.

## Self-improvement

The self-maintenance path remains separate from normal work:

```text
recurring trace weakness
        ↓
minimal candidate patch
        ↓
isolated regression + security evaluation
        ↓
proposal artifact / exact diff
        ↓
human promotion gate
```

A candidate cannot rewrite tests/evals/security/promotion logic to grade itself. Skills are also quarantined and human-promoted.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/harness.example.yaml config/harness.yaml
pytest -q
adaptive-harness doctor -c config/harness.yaml
adaptive-harness run "Inspect this repository and fix the failing tests" -c config/harness.yaml --profile code
adaptive-harness chat -c config/harness.yaml --session main
adaptive-harness economy-status -c config/harness.yaml
```

Optional private Telegram channel after local allowlist/token configuration:

```bash
adaptive-harness telegram -c config/harness.yaml
```

## Validation and research

See:

- `docs/ARCHITECTURE.md`
- `docs/TEAM_ORCHESTRATION.md`
- `docs/ADAPTIVE_COMPUTE_ECONOMY.md`
- `docs/EVIDENCE_GROUNDED_COMPUTE.md`
- `docs/VALIDATION.md`
- `docs/THREAT_MODEL.md`
- `docs/SELF_IMPROVEMENT.md`
- `docs/CHANNELS.md`
- `docs/RESEARCH_EVIDENCE.md`
- `docs/PRODUCTION_ROADMAP.md`

The architecture draws on work around composable agents, sparse/multi-agent communication, cost-aware routing, adaptive test-time compute, semantic caching, trajectory calibration, evidence-conditioned execution, agent evaluation/reward hacking, long-horizon state and empirical self-improvement. The repository intentionally treats those results as design evidence rather than proof that one architecture is universally optimal.

## Explicit non-claims

v0.5 does **not** yet claim:

- provider-backed dollar/token superiority over v0.4, Hermes, fixed broadcast MoA or any other system;
- statistically meaningful cross-domain quality superiority;
- production-scale cache precision measured on a representative trace corpus;
- hostile-code microVM isolation;
- persistent multi-agent code-writing worktrees with merge arbitration;
- a full external long-horizon Manage–Execute–Audit state plane.

Those are empirical/engineering gates, not wording problems. A future version becomes champion only when the relevant held-out evidence demonstrates it.