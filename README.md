# Adaptive Agent Harness

A model-agnostic control plane for long-lived AI agents that can research, scrape, code, use tools, work as a governed team, learn from traces, and propose improvements **without being allowed to silently rewrite the live trust boundary**.

The design target is not maximum autonomy or maximum agent count. It is:

> **maximum useful autonomy and task quality for the minimum justified compute, under measurable evidence, bounded capabilities, durable state, independent verification, and human-governed evolution.**

> Status: frontier reference implementation, 17 August 2026. Version **0.4.0** currently passes **65/65 reference tests** in GitHub Actions. v0.4 adds a local-first Adaptive Compute Economy and a guarded multi-space semantic work cache on top of the v0.3 sparse hierarchical team runtime. Production superiority and live cost savings are deliberately not claimed until representative provider-backed held-out benchmarks exist.

## Architecture

```text
human / Telegram / API
          │
          ▼
   governed parent agent
          │
   capability + evidence policy
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
 worker  worker   worker        independent branches in parallel
   │      │        │
   └── compact evidence ──┐
                          ▼
                    lead synthesis
                          │
                 uncertainty remains?
                    │           │
                   no          yes
                    │           │
                    ▼           ▼
                   stop   targeted follow-up
                                │
                                └────► bounded next round
```

The parent remains the authority. Team outputs are untrusted deliberation data; persistent writes and external effects still pass through the normal parent policy, evidence, verification, and approval path.

## v0.4 — Adaptive Compute Economy

The team no longer asks only “cheap model or premium model?”. It can choose a **shape of computation** for each work item:

```text
stop
cheap_single
cheap_pair
primary_single
mixed_pair
```

`cheap_pair` and `mixed_pair` are independent attempts with deliberately different instructions, not a broadcast debate. Their compact results are reconciled by the lead synthesizer.

The local `ComputeMarket` scores candidate strategies using:

- task family and difficulty;
- critical vs non-critical status;
- current team confidence;
- remaining dollar budget when supplied;
- empirical strategy success in the same task bucket;
- actual provider-reported historical cost;
- historical synthesis-confidence gain;
- an exploration bonus while evidence is sparse.

Cold-start routing is transparent configuration. After enough observations, empirical outcomes can overturn it. For example, a cheap pair that repeatedly fails code-review tasks can lose to one primary-model attempt for that region, while remaining preferred for another task family.

The learned policy is **inside** the hard trust envelope. It cannot increase `max_agents`, `max_rounds`, capability scopes, approval policy, or the run cost ceiling.

## Cost control

The harness attacks multi-agent cost at several layers instead of relying on one trick:

1. **Zero-token complexity gate** — obvious simple work never enters the team plane.
2. **Sparse dependency DAG** — workers receive only dependencies they need, not every peer transcript.
3. **Context capsules** — root goal + bounded subtask + relevant constraints/evidence, rather than the full growing conversation.
4. **Adaptive coalition size** — one cheap rollout, two diverse cheap rollouts, one primary rollout, a mixed pair, or no additional compute.
5. **Adaptive cheap-worker step budget** — cheap workers receive fewer loop steps on easier tasks.
6. **Early stop** — high confidence and low marginal expected gain can stop further computation.
7. **Exact + semantic work reuse** — verified prior work can avoid repeated inference.
8. **Provider prompt-prefix caching compatibility** — stable policy/tool material stays in stable prompt prefixes where possible.
9. **Parent budget accounting** — planner, workers, synthesis, verifier and nested team model costs are charged to the parent run when providers report them.

`max_agents` in v0.4 bounds **actual child attempts**, including redundant pairs. A pair cannot silently double the swarm beyond the configured limit.

## Multi-space semantic work cache

v0.3 used a single local semantic vector. v0.4 separates several views of a work unit:

```text
intent      what is being attempted
procedure   audit / inspect / validate / repair / research / ...
entities    paths, URLs, IDs, versions and other material targets
full        complete normalized text
```

Direct semantic reuse is intentionally difficult. A prior work product must be verified/direct-eligible, match the current workspace fingerprint and freshness rules, exceed the hybrid similarity threshold, preserve material entity overlap, and preserve procedural compatibility. Otherwise it is only a **reference hint** that a worker must re-check.

Equivalent procedural verbs are canonicalized into families (`audit/review/inspect`, `test/verify/validate`, `debug/fix/refactor`, etc.) instead of weakening safety thresholds.

The cache also exposes an online calibration hook. Negative reuse feedback can tighten the direct-reuse threshold by namespace/task kind. Vector similarity is therefore retrieval evidence, never a correctness or authorization oracle.

The default backend is **SQLite + local deterministic feature hashing**. No Qdrant, pgvector, embedding API, or external service is required. A richer vector backend can replace indexing later without becoming a capability authority.

## Core trust and runtime features

- **Model-agnostic gateway:** LiteLLM-backed roles work with OpenRouter, Replicate, OpenAI-compatible providers, Anthropic, Gemini, Ollama, and other supported endpoints.
- **Task profiles:** generic, code, research, scraping, operations, advisory. Profiles can reduce the capability surface; they cannot expand local policy.
- **Exact action approvals:** external side effects and privileged actions pause with a durable checkpoint and SHA-256 action fingerprint.
- **Blind verification:** high-impact actions are independently reconstructed/checked before approval and again before execution after resume.
- **Execution ledger:** non-idempotent actions are claimed before execution; unknown crash states block blind retry.
- **Evidence gate:** deterministic requirements can block commitment until evidence exists.
- **Durable state:** SQLite reference stores for traces, checkpoints, approvals, execution ledger, memory, routing/economy statistics, cache, and Telegram cursor.
- **Provenance:** public web and MCP outputs are `UNTRUSTED_EXTERNAL` data, not privileged instructions.
- **MCP default deny:** remote tool prose cannot grant permissions; schemas are locally reviewed/pinned and remote output stays untrusted.
- **HTTP safety:** public HTTP(S) only, DNS/private-address checks, revalidated redirects, bounded content.
- **Sandboxing:** no network, dropped Linux capabilities, resource limits, no implicit model-selected image pull.
- **Read-only specialist sandbox:** team workers may test/build in a disposable container-local copy while the host workspace remains read-only.
- **Runtime self-write firewall:** ordinary task agents cannot rewrite the harness trust kernel when operating in the harness checkout.
- **Self-improvement lane:** recurring weakness → minimal candidate patch → isolated regressions/security → exact human promotion. No auto-commit/push by the agent.
- **Skill quarantine:** repeated cross-goal evidence plus poisoning checks and human promotion are required before reusable skills become trusted procedural assets.
- **Telegram channel:** default-deny allowlist, durable sessions/cursor, exact approve/reject callbacks, and cross-session approval protection.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/harness.example.yaml config/harness.yaml
pytest -q
adaptive-harness doctor -c config/harness.yaml
adaptive-harness run "Inspect this repository and explain the highest-risk defects" \
  -c config/harness.yaml --profile code
adaptive-harness chat -c config/harness.yaml --session main
```

For private Telegram control after adding an allowlisted user ID and bot token to the environment:

```bash
export TELEGRAM_BOT_TOKEN='...'
adaptive-harness telegram -c config/harness.yaml
```

No secret should be committed to YAML or Git.

### Revert to v0.3-style team economics

The v0.4 path is feature-gated. In configuration:

```yaml
team:
  economy:
    enabled: false
```

This retains the sparse hierarchical team runtime while using the earlier static/empirical cheap-vs-primary router and v0.3 semantic cache.

## Validation boundary

GitHub Actions installs the package, runs the source test suite, compile checks and Ruff critical-error checks, rebuilds a source manifest and deterministic ZIP, extracts that ZIP into a clean directory, and runs the same tests/compile/lint checks again from the archive before committing the binary snapshot.

Passing those tests is **not** a claim that v0.4 is universally better or cheaper than Hermes, a fixed MoA, or every private harness. The next empirical gate is a held-out workload comparing at least:

- single primary agent;
- single cheap agent;
- fixed broadcast MoA;
- sparse v0.3;
- v0.4 adaptive compute without multi-space cache;
- v0.4 adaptive compute + multi-space cache;
- future learned policies.

Measure task success with deterministic evidence where possible, input/output/cached tokens, dollars, latency, child attempts, rounds, false direct-cache reuse, quality gain per dollar, and human interventions.

Real provider cost/quality benchmarks, real prompt-cache hit rates, real Telegram delivery, hostile-code microVM isolation, production-scale vector stores, and a complete long-horizon external task-state plane remain outside the current verified claim.

## Research basis

The design synthesizes rather than copies patterns from:

- Anthropic — effective agents, context/tool design, long-running harnesses, multi-agent research.
- OpenAI — agent evals, trace grading, Codex loop/sandbox engineering.
- Nous Research Hermes — practical delegation and prompt caching patterns.
- Mixture-of-Agents — layered collaboration baseline.
- AgentPrune / AgentDropout — redundancy and sparse communication.
- LLMRouter / SeqRoute / R2-Router — quality/cost and sequential resource routing.
- Adaptive Test-Time Compute Allocation — per-instance compute under global constraints.
- Dynamic Coalition Formation and Communication Pricing — coalition/communication value.
- VectorQ / Krites / semantic-cache calibration work — guarded semantic reuse.
- Self-Harness / Darwin Gödel Machine / hierarchical auto-harness work — empirical, constrained self-improvement.
- RETRACE / ECLoop / AgentLens — evidence and independent verification before commitment.
- trajectory-poisoning research — procedural learning as a security boundary.

See [Architecture](docs/ARCHITECTURE.md), [Team orchestration](docs/TEAM_ORCHESTRATION.md), [Adaptive Compute Economy](docs/ADAPTIVE_COMPUTE_ECONOMY.md), [Channels](docs/CHANNELS.md), [Research evidence](docs/RESEARCH_EVIDENCE.md), [Self-improvement](docs/SELF_IMPROVEMENT.md), [Threat model](docs/THREAT_MODEL.md), [Validation](docs/VALIDATION.md), and [Production roadmap](docs/PRODUCTION_ROADMAP.md).

## Important boundary

No public implementation can truthfully be certified as “the best harness on the planet” across every domain and private system. This repository instead tries to make improvement **falsifiable**: a future strategy or self-modification should become champion only when it beats the current one on held-out evidence without weakening safety, cost, latency, or human control.
