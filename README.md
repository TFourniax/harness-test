# Adaptive Agent Harness

A model-agnostic control plane for long-lived AI agents that can research, scrape, code, use tools,
operate as bounded teams, preserve durable mission state, learn from evidence, and improve their own
orchestration **without being allowed to silently weaken the live trust boundary**.

The design objective is:

> **maximum task quality for the minimum justified compute, under evidence, bounded capabilities,
> durable state, independent verification, recoverable execution, and human-governed evolution.**

**Current frontier snapshot: v0.7.0 — Distributed Reasoning Cells.** v0.7 adds recoverable task leases,
hierarchical dollar escrow, separate cell/leaf-attempt ceilings, explicit partial/blocked completion
states, a lease-safe Mission Dispatcher, and a zero-token gate that prevents paying for hierarchy on
flat work. The release remains a research/reference implementation: provider-backed held-out benchmarks
are still required before claiming universal quality or cost superiority.

## Architecture

```text
human / Telegram / API
          │
          ▼
   governed parent agent
          │
 capability + approval policy
          │
          ├──────── simple task ───────────────► single-agent loop
          │
          ├──────── decomposable task ─────────► sparse team / adaptive compute
          │
          └──────── durable mission ───────────► Mission State Plane
                                                    │
                                           revision + task lease
                                                    │
                                             context budget
                                                    │
                                       zero-token hierarchy gate
                                             │              │
                                           flat          structured
                                             │              │
                                             ▼              ▼
                                           leaf       reasoning cell
                                                           │
                                                   child $ escrows
                                                           │
                                               independent child cells
                                                           │
                                                   real leaf attempts
                                                           │
                                              environment evidence
                                                           │
                                              verification certificate
                                                           │
                                      COMPLETE + sufficient evidence?
                                              │             │
                                             yes            no
                                              │             │
                                             DONE      BLOCKED/replan
```

The parent remains the authority. Team/cell outputs are deliberation data, not permissions. Persistent
writes and external effects still pass through the normal capability, evidence, independent verification
and exact human-approval path.

## What changed in v0.7

### Distributed Reasoning Cells

Recursive reasoning is treated as a resource-allocation problem, not a default swarm pattern.

- `max_cells` limits orchestration nodes.
- `max_leaf_attempts` limits real model-worker rollouts.
- `max_depth` limits recursion.
- child cells receive dollar escrows carved from the parent envelope.
- planner cost is charged before budget is distributed.
- leaf `Goal.max_cost_usd` is exactly the leaf escrow allowance.
- unused child reservation returns to the parent after settlement.

A cell itself is not counted as a leaf agent. Conversely, two independent model rollouts consume two
leaf-attempt slots even when they belong to the same cell.

### Recoverable task leases

Durable mission tasks have explicit ownership with TTL and heartbeat. A crashed dispatcher can release
work through lease expiry; live work cannot be silently stolen. An `ACTIVE` task without concrete
dispatcher lease history is never auto-requeued.

If lease ownership is lost during execution, the computed result is non-committable even if the answer
looks good.

### Explicit completion semantics

Reasoning cells distinguish:

```text
COMPLETE  all scheduled work completed
PARTIAL   scheduled analytical work ran, but not all branches succeeded
BLOCKED   promised work could not run because a hard resource/scheduler ceiling was hit
FAILED    no acceptable completed result
```

Only `COMPLETE` maps to `success=True`, and even that is insufficient for durable mission completion
without a task-relevant verification certificate.

### Mission Dispatcher

The dispatcher links v0.6 durable state to v0.7 distributed cognition. It claims one dependency-ready
task, revalidates readiness, transitions it using the mission revision, keeps the lease alive, executes
under bounded context/cells/escrow, resolves concrete evidence from the trace store, and lets the
canonical State Plane finalize the task only when the evidence gate passes.

`mission_dispatch_next` requires `expected_revision` and is non-idempotent. The existing execution ledger
therefore deduplicates an exact retry, while a later dispatch requires reading fresh state and supplying
the new revision.

### Zero-token hierarchy gate

A difficult task is not automatically a hierarchical task. Before paying a cell-lead model call, a
local deterministic gate requires evidence that the task is also structurally decomposable. Flat work
goes directly to a leaf. The same gate is used in durable mission dispatch.

## Foundations retained

### Evidence-Grounded Compute Economy — v0.5

The adaptive compute market can choose:

```text
stop
cheap_single
cheap_pair
primary_single
mixed_pair
```

Strategy learning is weighted by verification evidence and decays over time. Model-only "success" has
little influence. Hard remaining-dollar budgets remain true ceilings.

The champion/challenger Policy Arena accepts only matched high-evidence benchmark trials for promotion
recommendations. Promotion still requires an external human governance decision bound to the exact
comparison fingerprint.

### Durable Mission Plane — v0.6

Canonical long-horizon state lives outside model context:

- optimistic mission revisions prevent silent last-writer-wins overwrites;
- task dependencies constrain completion;
- evidence IDs must resolve to real executed trace observations;
- stale evidence cannot certify new work;
- workers read bounded context slices rather than owning canonical state;
- only the governed parent receives mission-state write tools;
- mission task count has a hard ceiling.

### Sparse teams and guarded reuse

The earlier foundations remain available for shallower work:

- sparse dependency DAG rather than broadcast transcript sharing;
- independent branches run concurrently;
- context capsules avoid full-transcript replication;
- multi-space semantic cache separates intent/procedure/entities/full text;
- direct cache reuse requires the normal provenance/freshness/workspace gates plus strong evidence;
- local SQLite + deterministic hashing is the default backend.

## Trust boundary

These constraints remain non-negotiable:

- external/privileged actions require configured exact human approval;
- approval is bound to the exact action fingerprint;
- high-impact actions are independently checked before approval and again before execution;
- remote MCP metadata is untrusted and cannot grant capabilities;
- profiles can reduce capability, never expand local policy;
- non-idempotent execution uses a durable exactly-once/reconciliation ledger;
- ordinary agents cannot rewrite the harness trust kernel while using the harness as a workspace;
- self-improvement candidates cannot alter their trusted exam/promotion boundary;
- verification, state, compute-policy, leases, escrow, cell-runtime and mission-dispatch components are
  protected from autonomous self-promotion.

## Model/provider layer

The runtime uses LiteLLM normalization and is not tied to one vendor. Role IDs may target OpenRouter,
Replicate/OpenAI-compatible endpoints, Anthropic, Gemini, Ollama and other LiteLLM-supported providers.
Roles include primary, planner, verifier, critic and cheap workers with per-role fallback/retry/timeout/
tool-mode settings.

## Human channels

The channel layer is transport-neutral. Telegram remains the first adapter and is default-deny:
allowlisted users, private chats by default, durable per-human/per-conversation sessions, exact approval
buttons, cross-session approval protection, and token-from-environment only. A channel is a transport
around the governed runtime, never a capability bypass.

## Self-improvement

```text
recurring trace weakness
        ↓
minimal candidate patch
        ↓
isolated regression + security evaluation
        ↓
proposal / exact diff
        ↓
human promotion gate
```

Skills are separately quarantined and human-promoted. A candidate cannot rewrite tests, evals, security
or promotion logic to grade itself.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/harness.example.yaml config/harness.yaml
pytest -q
adaptive-harness doctor -c config/harness.yaml
adaptive-harness run "Inspect this repository and fix the failing tests" -c config/harness.yaml --profile code
adaptive-harness mission-create "Ship the project with verified acceptance criteria" -c config/harness.yaml
adaptive-harness mission-status <mission-id> -c config/harness.yaml
adaptive-harness mission-dispatch <mission-id> -c config/harness.yaml
adaptive-harness distributed-status -c config/harness.yaml
adaptive-harness economy-status -c config/harness.yaml
```

Optional private Telegram channel after local allowlist/token configuration:

```bash
adaptive-harness telegram -c config/harness.yaml
```

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/TEAM_ORCHESTRATION.md`
- `docs/ADAPTIVE_COMPUTE_ECONOMY.md`
- `docs/EVIDENCE_GROUNDED_COMPUTE.md`
- `docs/DURABLE_MISSION_PLANE.md`
- `docs/DISTRIBUTED_REASONING_CELLS.md`
- `docs/VALIDATION.md`
- `docs/THREAT_MODEL.md`
- `docs/SELF_IMPROVEMENT.md`
- `docs/CHANNELS.md`
- `docs/RESEARCH_EVIDENCE.md`
- `docs/PRODUCTION_ROADMAP.md`

## Explicit non-claims / next frontier

v0.7 does **not** yet claim provider-backed superiority over a single strong agent, the sparse flat team,
Hermes, broadcast MoA, or another agent framework. It also does not yet prove production hostile-code
isolation or statistically significant long-horizon cost savings.

The next research/engineering frontier is **independence-aware marginal compute**: do not buy a second or
third agent because a counter says there is room; buy it only when its expected new reasoning/evidence
channel justifies its marginal cost. Selection quality and evidence independence should matter more than
raw agent count.
