# Durable Mission Plane — v0.6

The v0.6 Mission Plane separates long-horizon task state from model context.

The model context is a temporary, budgeted view. The mission database is the canonical progress record.
This distinction prevents a long-running agent from treating a compressed conversation, an old summary,
or a worker's self-report as authoritative task state.

## Core invariants

1. **Revisioned state, not last-writer-wins prose**
   - Every mission mutation is bound to an expected revision.
   - A stale worker cannot silently overwrite a newer state transition.
   - Concurrent branches must reload/reconcile after a revision conflict.

2. **Dependencies constrain completion**
   - A dependent task cannot be finalized while required predecessors remain unresolved.
   - Unresolved state stays unresolved instead of being inferred away by the model.

3. **Completion requires task-relevant evidence**
   - `DONE` is not accepted because a worker says it finished.
   - Evidence references must resolve to concrete tool observations already emitted by the governed runtime.
   - Model-invented call IDs are rejected.
   - Evidence must be fresh enough for the mission/task being finalized; an old successful check cannot certify a new task.
   - The existing v0.5 verification engine reconstructs a certificate and estimates postcondition coverage.

4. **State writes remain parent-governed**
   - Child workers may receive read/audit access to bounded mission slices.
   - Canonical mission mutations stay on the governed parent path.
   - Mission state therefore does not become a second capability authority.

5. **Hard mission-size ceiling**
   - The local store enforces `max_tasks_per_mission`.
   - A planner cannot create an unbounded recursive task graph simply by emitting more tasks.

## State Plane vs Context Plane

```text
                    canonical / durable
                   ┌───────────────────┐
 environment ─────►│   Mission State   │◄──── governed parent mutations
 observations      │ tasks/facts/revs  │
                   └─────────┬─────────┘
                             │
                     relevance + budget
                             │
                             ▼
                   ┌───────────────────┐
                   │   Context Plane   │
                   │ bounded state view│
                   └─────────┬─────────┘
                             │
                             ▼
                       model / worker
```

A worker never owns the whole truth merely because it received a prompt. It receives the smallest useful
slice of the current mission state under the configured context budget.

## Policy Uncertainty Lab

v0.6 also adds a paired held-out evaluation layer above the v0.5 champion/challenger arena.

- Live and shadow traces remain diagnostic only.
- Promotion evidence is built from matched benchmark task keys.
- The unit of resampling is the task, preserving champion/challenger pairing.
- Deterministic bootstrap intervals are computed for quality delta, relative cost delta and success delta.
- A challenger is held when its average looks attractive but the interval still crosses an unacceptable regression.
- Promotion remains a governance action; the lab can recommend but cannot self-promote a policy.

The policy storage schema remains backward-compatible with v0.5. The API accepts both `passed=` and
`success=` as input terminology, rejects contradictions, and persists one canonical `passed` field.

## Local-first reference backend

The validated reference path requires no state SaaS and no vector service:

- SQLite for canonical mission state;
- the existing trace store for evidence provenance;
- local context selection;
- the existing verification/capability/approval planes.

PostgreSQL, distributed queues or a vector backend may replace storage/indexing at production scale, but
none of those systems is allowed to become an authorization oracle.

## What v0.6 does not claim

v0.6 establishes durable state and evidence-backed transitions; it does not yet claim that an arbitrary
multi-day real-world mission can run unattended in production. The next architecture layer should add
recoverable task leases, hierarchical budget escrow and bounded sub-orchestrators, then benchmark those
mechanisms on representative long-horizon workloads.
