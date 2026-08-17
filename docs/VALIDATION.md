# Validation Snapshot

Date: 2026-08-17
Version: **0.7.0**

This document records what the automated reference build has actually checked. It is narrower than a
production certification and deliberately avoids claims of universal quality/cost superiority.

## Final v0.7 package validation

The direct-source v0.7 release pipeline completed successfully:

- package/console entrypoint resolved as **0.7.0 / `adaptive_harness.v07_cli:app`**;
- `pytest -q`: **132 passed**;
- `python -m compileall -q src tests`: passed;
- Ruff critical-error checks (`E9,F63,F7,F82`): passed;
- deterministic source manifest generated from committed source;
- deterministic `artifacts/adaptive-agent-harness-0.7.0.zip` built;
- ZIP size in the validated run: **235454 bytes**;
- the ZIP was extracted into a clean directory;
- the complete **132-test + compile + Ruff** validation passed again from the extracted archive;
- GitHub Actions committed the verified snapshot back to the branch.

Final v0.7 ZIP SHA-256:

```text
9d20478bc855e88c4f920cf7a2a4359232859aed6d56bb218aeac468a8b72fd2
```

## Core trust/runtime coverage retained

The suite continues to cover:

- capability policy and profile ceilings;
- exact high-impact approval and resume;
- independent high-impact verification;
- non-idempotent execution ledger and uncertain-completion reconciliation;
- evidence gate;
- session persistence;
- tool JSON-schema validation;
- HTTP SSRF/private-destination and redirect handling;
- default-deny MCP import and schema fingerprint checks;
- runtime self-write firewall;
- self-improvement root-of-trust protection;
- isolated regression/security evaluation and human promotion;
- skill quarantine;
- Telegram default-deny access, durable cursor/session identity and cross-session approval protection.

## Evidence-grounded compute coverage retained

v0.5/v0.6 tests remain part of the v0.7 suite and cover:

- verification certificates bound to the assigned task;
- model-only success carrying very weak evidence weight;
- deterministic failures producing refutation;
- unrelated passing checks not laundering unrelated claims;
- process-aware confidence calibration;
- evidence-weighted compute-market learning and temporal decay;
- hard remaining-dollar budget semantics;
- adaptive follow-up when confidence is high but evidence is weak;
- guarded multi-space semantic reuse;
- paired benchmark policy comparison;
- human promotion bound to the exact benchmark comparison fingerprint;
- paired/bootstrap Policy Lab uncertainty above the Policy Arena.

## Durable Mission Plane — v0.6 coverage

Tests validate:

- canonical mission state outside model context;
- optimistic mission revisions and stale-write rejection;
- task dependencies constraining completion;
- hard mission task-count ceiling;
- bounded mission context packing;
- parent-write / child-read state-tool split;
- evidence IDs resolving only to real executed trace observations;
- invented evidence references being rejected;
- evidence freshness relative to mission/task creation;
- `DONE` requiring a sufficiently strong task-relevant certificate;
- unrelated global mission revision changes being reconcilable only when the target task contract is
  unchanged.

## Distributed Reasoning Cells — v0.7 coverage

### Recoverable leases

Tests cover:

- one active lease per mission/task;
- competing owner rejection;
- TTL expiry and recovery;
- heartbeat extension;
- wrong-owner finish rejection;
- completed/released/expired states;
- `ACTIVE` mission work being auto-requeued only when concrete dispatcher lease history expired;
- `ACTIVE` work with no dispatcher lease history remaining untouched.

### Hierarchical dollar escrow

Tests cover:

- root and child escrow creation;
- sibling reservations not over-allocating their parent;
- charges never exceeding currently available allowance;
- nested child reservations;
- child settlement propagating only **actual spend** upward;
- unused reservation returning to the parent;
- cancellation of untouched reservations;
- refusal to settle a parent while open child reservations remain.

### Cell runtime

Tests cover:

- separate `max_cells` and `max_leaf_attempts` accounting;
- two child cells creating two real leaf attempts rather than counting the orchestration node as a
  third worker;
- parallel child execution;
- planner cost charged before child budget split;
- one-child decomposition collapsing back to a direct leaf;
- leaf spend being unable to exceed its escrow;
- leaf-attempt exhaustion producing `BLOCKED`, not false success;
- executed analytical disagreement/failure producing `PARTIAL`, not `COMPLETE`;
- `success=True` being reserved for `COMPLETE` cells.

### LLM hierarchy adapter

Tests cover:

- deterministic zero-token rejection of flat work even when it is difficult;
- structured difficult work becoming eligible for hierarchy;
- duplicate proposed child tasks being normalized/deduplicated;
- fewer than two distinct children collapsing to direct leaf execution;
- hierarchical child IDs and budget weights;
- leaf `Goal.max_cost_usd` exactly matching the leaf escrow allowance;
- cheap/primary role selection without granting new capabilities.

### Mission Dispatcher

Tests cover:

- leasing and activating exactly one dependency-ready task;
- bounded mission-context delivery;
- verified `COMPLETE` computation finalizing the durable task;
- model-only apparent completion becoming `BLOCKED` rather than `DONE`;
- resource-blocked cells never finalizing durable state;
- expired lease recovery;
- unrelated mission revision advancement being safely rebased only when the target task contract is
  unchanged;
- loss of ownership preventing commit;
- `mission_dispatch_next` requiring the exact just-read `expected_revision` and using the existing
  non-idempotent execution ledger.

### Trust-kernel protection

The immutable self-evolution boundary now includes the v0.7 coordination plane:

- distributed-control leases/escrow;
- cell runtime;
- mission dispatcher;
- hierarchy adapter;
- v0.7 config/CLI;
- the existing state, verification, compute-policy, approval, evaluation and promotion components.

These files can be changed through normal reviewed development, but cannot be silently auto-promoted by
the harness itself.

## Not live-tested / not yet proven

v0.7 deliberately does **not** claim:

- statistically meaningful dollar/token superiority on representative OpenRouter, Replicate,
  Anthropic, OpenAI-compatible or other provider workloads;
- universal superiority over one strong primary agent, sparse flat teams, Hermes, fixed broadcast MoA
  or another harness;
- that recursion is beneficial for every difficult task;
- calibrated production prompt/KV-cache hit rates;
- production semantic-cache false-direct-reuse rate over a representative trace corpus;
- production hostile-code isolation; microVM/gVisor/Firecracker-class controls remain a stronger target
  than the reference Docker path;
- real external side-effect adapter safety without staging/provider-specific acceptance tests;
- production-scale Postgres/Qdrant/queue performance;
- that source diversity or agent disagreement alone proves factual correctness.

## Next empirical gate

Held-out benchmarks should compare at least:

1. single primary;
2. single cheap;
3. fixed homogeneous N-agent broadcast;
4. sparse flat team;
5. v0.5 evidence-grounded adaptive compute;
6. v0.6 durable mission plane without recursive cells;
7. v0.7 distributed reasoning cells;
8. v0.7 with hierarchy forcibly disabled;
9. future independence-aware marginal-compute policy.

Track task success using domain-grounded/oracle evidence where possible, evidence strength, dollars,
input/output/cached tokens, latency, real leaf attempts, cells opened, hierarchy depth, lease recovery,
blocked/partial rates, cache reuse safety, quality gain per dollar and human intervention.

Held-out truth must stay outside candidate-writable boundaries. A learned orchestration policy remains a
challenger until reproducible evidence and the exact human-governed promotion gate approve it.
