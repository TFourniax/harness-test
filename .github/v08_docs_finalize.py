from pathlib import Path

README = r'''# Adaptive Agent Harness

A model-agnostic control plane for long-lived AI agents that can research, scrape, code, operate tools,
run bounded teams-of-teams, preserve durable mission state, learn from evidence, and improve their own
orchestration **without being allowed to silently weaken the live trust boundary**.

The design objective is:

> **maximum task quality for the minimum justified compute, under evidence, bounded capabilities,
> durable state, independent verification, recoverable execution, and human-governed evolution.**

**Current frontier snapshot: v0.8.0 — Independence-Aware Marginal Compute.** v0.8 keeps the v0.7
Distributed Reasoning Cells, leases, dollar escrows and Mission Dispatcher, but stops treating an open
agent slot as a reason to buy another model call. A second/third rollout is purchased only when its
expected **new verification channel** is worth its marginal cost.

The release remains a research/reference implementation. Provider-backed held-out benchmarks are still
required before claiming universal quality or cost superiority.

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
          └──────── durable mission ───────────► canonical Mission State Plane
                                                    │
                                           revision + task lease
                                                    │
                                             context budget
                                                    │
                                       zero-token hierarchy gate
                                             │              │
                                           flat          structured
                                             │              │
                                             └──────┬───────┘
                                                    ▼
                                            leaf/cell subtree
                                                    │
                                       atomic real-rollout budget
                                                    │
                                             attempt #1
                                                    │
                                        verification certificate
                                                    │
                                marginal new evidence worth its cost?
                                          │                    │
                                         no                   yes
                                          │                    │
                                         stop         blind attempt #2/#3
                                                               │
                                              canonical evidence channels
                                                               │
                                              evidence-first selection
                                                               │
                                                       combined audit
                                                               │
                                      COMPLETE + sufficient evidence?
                                              │             │
                                             yes            no
                                              │             │
                                             DONE      BLOCKED/replan
```

The parent remains the authority. Team/cell/panel outputs are deliberation data, not permissions.
Persistent writes and external effects still pass through the normal capability, evidence, independent
verification and exact human-approval path.

## v0.8 — Independence-Aware Marginal Compute

### Buy verification, not agent count

A panel starts with one real rollout. The next rollout is optional.

For each task bucket and panel slot, the local market learns:

- marginal increase in evidence strength;
- marginal increase in certificate score;
- marginal increase in task/postcondition coverage;
- measured independence from existing attempts;
- actual marginal cost when providers report it.

Diversity is **not** the reward. It is only an opportunity signal. The actual learning target is the
extra verifiable task coverage produced by the next attempt.

### Exact model-call accounting

`LeafAttemptBudget` is shared atomically across concurrent cells and panels. A slot is claimed only
immediately before a real model rollout.

A panel configured for three attempts that stops after its first proof therefore consumes one leaf
attempt, not three. A panel also cannot hide extra model calls by under-reporting its own attempt count.

### Blind method/model lanes

Attempts never see peer answers.

Typical lanes:

```text
code       postcondition → falsification → invariants
research   primary source → disconfirming source → independent source family
scraping   schema → adversarial sample → independent extraction
operations current state → failure/recovery mode → alternate postcondition
```

Hard/critical default tier pattern:

```text
1. primary
2. cheap falsifier
3. primary only if still justified
```

If the preferred premium lane does not fit the remaining escrow but cheap does, it degrades to cheap.
If even cheap cannot fit, no call is purchased.

### Canonical evidence channels

Exact `call_id` values remain the audit trail, but they are not an independence metric. Every tool
observation now receives a locally generated:

```text
provenance_fingerprint = SHA256(canonical(tool_name, arguments))
```

Two agents fetching the same URL or running the same concrete tool invocation therefore remain one
evidence channel even when their call IDs differ.

Tool-returned metadata cannot override locally reviewed `risk`, `source`, or
`provenance_fingerprint` fields.

### Conservative independence score

Current signal weights:

```text
55% canonical evidence-channel novelty
20% semantic answer novelty
15% method novelty
10% model/tier novelty
```

Without a new evidence channel, even maximally different prose/method/model remains below 0.5.
Candidate independence against an existing panel uses the **minimum** pairwise score, so being novel
relative to one worker does not hide near-duplication with another.

### No majority vote, no final LLM judge

Selection is deterministic and evidence-first:

```text
VERIFIED > SUPPORTED > UNVERIFIED > REFUTED
then evidence strength / coverage / deterministic proof / independent sources / score
```

A minority attempt with a task-bound deterministic proof can beat multiple plausible answers. A
material deterministic refutation in the combined evidence can prevent optimistic panel success.

There is no final debate/synthesis model call, avoiding both extra cost and herding around a judge.

### Learn conservatively

The default cold-start `min_utility` is intentionally conservative (`0.120`).

After the sample floor, v0.8 no longer acts on raw averages. It uses a **one-sided 90% lower confidence
bound** for marginal verification gain and independence. Noisy occasional wins therefore do not look as
reliable as consistently useful second attempts.

Historical empirical cost is rechecked against the live escrow and can veto an attempt even if the
cold-start price prior would have fit.

## v0.7 foundation — Distributed Reasoning Cells

v0.8 reuses the exact same distributed cognition plane:

- separate `max_cells`, `max_leaf_attempts`, `max_depth` hard ceilings;
- recoverable task leases with TTL/heartbeat;
- nested dollar escrows preventing sibling over-allocation;
- explicit `COMPLETE / PARTIAL / BLOCKED / FAILED` semantics;
- deterministic zero-token hierarchy gate;
- Mission Dispatcher connecting durable tasks to bounded cells;
- loss of lease ownership makes computed output non-committable;
- `mission_dispatch_next` is revision-bound and non-idempotent through the execution ledger.

With `marginal_diversity.enabled: false`, the optional panel hooks are `None` and the v0.7 single-leaf
behavior remains available as a clean benchmark/rollback baseline.

## v0.6 foundation — Durable Mission Plane

Canonical long-horizon state lives outside model context:

- optimistic mission revisions prevent silent last-writer-wins overwrites;
- task dependencies constrain completion;
- evidence IDs must resolve to real executed trace observations;
- stale evidence cannot certify new work;
- workers read bounded state/context slices;
- only the governed parent receives mission-state write tools;
- durable `DONE` requires task-relevant evidence.

## v0.5 foundation — Evidence-Grounded Compute Economy

The shallower adaptive market can still choose:

```text
stop / cheap_single / cheap_pair / primary_single / mixed_pair
```

Strategy learning is weighted by verification evidence and decays over time. The champion/challenger
Policy Arena accepts only matched high-evidence benchmark trials for promotion recommendations, and
actual promotion remains bound to an external human approval fingerprint.

## Trust boundary

Non-negotiable constraints remain:

- external/privileged actions require configured exact human approval;
- approval is bound to the exact action fingerprint;
- high-impact actions are independently checked before approval and again before execution;
- profiles can reduce capability, never expand local policy;
- MCP/web/tool output cannot grant capabilities;
- non-idempotent execution uses a durable exactly-once/reconciliation ledger;
- ordinary agents cannot rewrite the harness trust kernel through workspace tools;
- self-improvement candidates cannot rewrite their trusted exam/promotion boundary;
- verification, state, compute-policy, leases, escrow, cell runtime, mission dispatch, marginal-diversity
  market and panel policy are protected from autonomous self-promotion.

## Model/provider layer

LiteLLM normalization keeps the runtime provider-agnostic. Role IDs may target OpenRouter,
Replicate/OpenAI-compatible endpoints, Anthropic, Gemini, Ollama and other supported providers.

## Human channels

Telegram remains the first transport adapter and is default-deny: allowlisted users, private chats by
default, durable sessions, exact approval buttons, cross-session approval protection, token from
environment only. Channels wrap the governed runtime; they never bypass it.

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

Skills remain separately quarantined and human-promoted.

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
adaptive-harness diversity-status -c config/harness.yaml
adaptive-harness economy-status -c config/harness.yaml
```

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/TEAM_ORCHESTRATION.md`
- `docs/EVIDENCE_GROUNDED_COMPUTE.md`
- `docs/DURABLE_MISSION_PLANE.md`
- `docs/DISTRIBUTED_REASONING_CELLS.md`
- `docs/INDEPENDENCE_AWARE_COMPUTE.md`
- `docs/VALIDATION.md`
- `docs/THREAT_MODEL.md`
- `docs/SELF_IMPROVEMENT.md`
- `docs/CHANNELS.md`
- `docs/RESEARCH_EVIDENCE.md`
- `docs/PRODUCTION_ROADMAP.md`

## Explicit non-claims / next empirical gate

v0.8 does **not** yet claim provider-backed superiority over one strong primary agent, a sparse flat
team, v0.7, Hermes, fixed broadcast MoA, or another framework.

The next empirical gate is a matched held-out benchmark comparing v0.7 single-leaf execution with v0.8
marginal panels while tracking real task success, verification strength, canonical evidence-channel
novelty, dollars, cached/input/output tokens, latency and human interventions.
'''

VALIDATION = r'''# Validation Snapshot

Date: 2026-08-17
Version: **0.8.0**

This document records deterministic/reference coverage. It deliberately does not embed the ZIP SHA-256:
`artifacts/adaptive-agent-harness-0.8.0.zip.sha256` is the authoritative checksum file and lives outside
the packaged ZIP, avoiding a self-referential checksum problem.

## Release gate

The v0.8 release is accepted only when the standard packaging workflow verifies all of the following on
the direct source branch:

- package identity is `0.8.0`;
- console entrypoint is `adaptive_harness.v08_cli:app`;
- full `pytest -q` suite passes;
- `python -m compileall -q src tests` passes;
- Ruff critical-error checks (`E9,F63,F7,F82`) pass;
- deterministic source manifest/ZIP are rebuilt;
- the produced ZIP is extracted into a clean directory;
- the complete pytest/compile/Ruff gate passes again from the extracted archive;
- only then may GitHub Actions commit the verified artifact/checksum snapshot.

An independent clone/re-extraction check should reproduce the same source and archive test gate.

## v0.8 Independence-Aware Marginal Compute coverage

Tests cover:

- exact atomic accounting of real panel model rollouts;
- a panel stopping after one strong proof consuming one real attempt, not its configured maximum;
- global leaf-attempt ceilings preventing hidden second/third calls across concurrent cells;
- blind method lanes that do not receive peer answers;
- deterministic evidence-first selection rather than majority vote;
- material deterministic refutation preventing optimistic panel success;
- hard/critical role diversification (`primary → cheap falsifier → primary`);
- primary-to-cheap affordability fallback and no-call behavior when even cheap cannot fit;
- conservative cold-start marginal utility;
- second/third attempt priors with a lower prior for deeper panel positions;
- learning measured marginal evidence/score/coverage gain rather than rewarding disagreement itself;
- repeatedly redundant second attempts teaching the market to stop;
- consistently useful independent attempts remaining purchasable;
- one-sided lower confidence bounds reducing the value of noisy historical wins;
- empirical marginal cost being rechecked against live remaining escrow.

## Canonical evidence-channel coverage

Tool/panel tests cover:

- stable `provenance_fingerprint = SHA256(tool + canonical arguments)` independent of ephemeral call ID;
- same tool+arguments producing the same evidence channel;
- different arguments producing different evidence channels;
- failed tool calls retaining canonical provenance for diagnostics;
- raw tool arguments not being copied into observation metadata;
- tool-returned metadata being unable to override local reviewed `risk`, `source`, or provenance;
- panel audit trails preserving exact call IDs while independence uses canonical channels;
- two distinct call IDs on the same canonical channel contributing zero evidence novelty;
- semantic/method/model disagreement without new evidence being bounded below 0.5 independence.

## v0.8/v0.7 A/B boundary

Tests cover:

- `v07_cli` keeping `panel_leaf_runner=None`;
- `v08_cli` injecting one shared panel policy into hierarchy and Mission Dispatcher;
- disabling marginal diversity leaving the v0.7 cell/mission plane intact;
- v0.8 marginal-policy files being inside the immutable self-evolution trust boundary;
- v0.7 builder remaining importable as a benchmark/rollback compatibility baseline.

## Distributed Reasoning Cells coverage retained

The v0.7 suite remains active and covers:

- one active lease per mission/task, heartbeat, expiry, owner checks and recovery;
- `ACTIVE` work without dispatcher lease history not being stolen;
- nested dollar escrow, sibling reservation exclusion and actual-spend settlement;
- separate cell and real-leaf ceilings;
- parallel child cells;
- planner cost before child budget split;
- `COMPLETE / PARTIAL / BLOCKED / FAILED` semantics;
- zero-token hierarchy gating;
- lease-safe Mission Dispatcher with evidence-gated durable finalization;
- revision-bound `mission_dispatch_next` through the non-idempotent execution ledger.

## Durable Mission Plane / earlier trust coverage retained

The full suite also keeps coverage for:

- canonical mission state and optimistic revisions;
- dependency-gated completion;
- fresh real trace evidence only;
- parent-write / worker-read state split;
- task-count and context budgets;
- capability/profile ceilings;
- exact high-impact approval and resume;
- blind high-impact verification;
- non-idempotent execution reconciliation;
- SSRF protections;
- default-deny MCP/schema pinning;
- runtime self-write firewall;
- self-improvement regression/security/human promotion;
- skill quarantine;
- Telegram default-deny identity/session/approval controls;
- evidence-grounded compute, guarded semantic reuse and champion/challenger policy governance.

## Not proven by this deterministic release gate

v0.8 deliberately does **not** claim:

- statistically significant provider-backed dollar/token superiority;
- calibrated real OpenRouter/Replicate/Anthropic/OpenAI-compatible price priors;
- universal superiority over one strong model, v0.7, sparse teams, Hermes or other agent harnesses;
- production semantic-cache false-reuse rates;
- production hostile-code isolation beyond the reference sandbox path;
- production-scale Postgres/Qdrant/queue performance;
- that canonical source diversity alone proves factual correctness.

## Next benchmark

Run matched held-out tasks across coding, research, scraping, operations and adversarial cases comparing:

1. single primary;
2. single cheap;
3. fixed homogeneous ensemble;
4. sparse flat team;
5. v0.7 distributed cells with one leaf attempt per cell;
6. v0.8 marginal panels;
7. v0.8 with learned marginal history reset vs warmed.

Measure domain-grounded success, certificate evidence strength/coverage, real model calls, canonical
provenance-channel novelty, dollars, tokens, latency, blocked/partial rates and human interventions.
Held-out truth remains outside candidate-writable boundaries.
'''

Path("README.md").write_text(README, encoding="utf-8")
Path("docs/VALIDATION.md").write_text(VALIDATION, encoding="utf-8")
print("Wrote v0.8 README and non-self-referential validation contract")
