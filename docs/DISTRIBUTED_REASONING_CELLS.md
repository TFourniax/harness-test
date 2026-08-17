# Distributed Reasoning Cells — v0.7

v0.7 turns recursive multi-agent reasoning into a bounded coordination problem instead of treating
"more agents" as a default quality knob.

The core design is deliberately closer to a small distributed system than to a prompt swarm:

```text
canonical mission task
        │
        ▼
 recoverable task lease ───── heartbeat / expiry / reconciliation
        │
        ▼
 root dollar escrow
        │
  zero-token hierarchy gate
        │
   ┌────┴─────────────────────────────────────┐
   │ flat / low structural value              │ deeply decomposable
   ▼                                          ▼
 leaf attempt                           bounded cell lead
                                             │
                                      child budget escrows
                                             │
                               ┌─────────────┼─────────────┐
                               ▼             ▼             ▼
                           child cell     child cell     child cell
                               │             │             │
                               └──── real leaf attempts ───┘
                                             │
                                      evidence references
                                             │
                                             ▼
                                  Mission Dispatcher audit
                                             │
                       COMPLETE + sufficient certificate?
                               │                       │
                              yes                      no
                               │                       │
                               ▼                       ▼
                              DONE            BLOCKED / replan
```

## Separate hard resources

v0.7 refuses to use one ambiguous `max_agents` counter for everything.

- **`max_cells`** limits orchestration nodes.
- **`max_leaf_attempts`** limits real model-worker rollouts.
- **`max_depth`** limits recursion.
- **dollar escrows** limit spend at every subtree.
- **task leases** limit concurrent ownership of durable mission work.

A cell existing does not automatically consume a leaf-agent slot. Conversely, two independent leaf
attempts consume two slots even if they are under one cell.

## Recoverable task leases

`DistributedControlStore` provides SQLite/WAL-backed leases with:

- one active lease per `(mission_id, task_id)`;
- owner identity;
- TTL;
- periodic heartbeat;
- explicit completion/release;
- expiry after a crashed worker;
- recovery only when concrete dispatcher lease history proves the previous ownership expired.

A durable task that is `ACTIVE` but has no dispatcher lease history is **not** automatically stolen or
requeued. This prevents the recovery mechanism from taking ownership of work started by another actor.

If heartbeat ownership is lost while a cell is executing, the computed result becomes non-committable.
Even a plausible/verified answer cannot be written to canonical mission state after lease loss.

## Hierarchical dollar escrow

Every reasoning tree receives an explicit budget envelope.

- A root escrow receives the task budget.
- A child cell receives a reservation from its parent.
- Siblings cannot reserve the same dollars twice.
- Planner calls are charged before child allocation.
- Leaf `Goal.max_cost_usd` is exactly its current escrow allowance.
- Child settlement propagates **actual spend**, not the full reservation, upward.
- Unused reservation is returned to the parent.
- Open child reservations prevent premature parent settlement.

The model may propose a budget weight, but it cannot increase the parent envelope.

## Zero-token hierarchy gate

Hierarchy is not free. Before paying a cell lead, a deterministic gate checks whether the task has
both enough difficulty and enough structural evidence of useful decomposition.

A short isolated bug can therefore be difficult while still going directly to one leaf worker. A long
multi-system architecture/audit task may justify a cell lead.

The same gate is applied to durable mission dispatch, so a mission containing many small tasks does not
pay one manager-model call per task.

## Cell completion semantics

v0.7 makes partial execution explicit:

- `COMPLETE`: every scheduled branch needed by the cell completed.
- `PARTIAL`: scheduled analytical work ran but one or more branches did not succeed.
- `BLOCKED`: promised work could not run because of a hard scheduler/resource condition such as leaf
  attempt or dollar budget exhaustion.
- `FAILED`: the cell produced no acceptable completed work.

`CellExecutionResult.success` is true **only** for `COMPLETE`.

This is important for long-horizon state: one successful branch cannot accidentally make a parent task
look finished when another scheduled branch never ran.

## Mission Dispatcher

`MissionDispatcher` connects the v0.6 State Plane to v0.7 cells.

For one task it:

1. recovers only demonstrably expired dispatcher work;
2. leases one dependency-ready `PENDING` task;
3. revalidates readiness after acquisition;
4. transitions it to `ACTIVE` using the current mission revision;
5. builds a token-budgeted state slice;
6. maintains the lease heartbeat while cognition runs;
7. executes the task under a bounded hierarchy/dollar escrow;
8. resolves evidence refs only from real trace observations;
9. rebuilds a v0.5 verification certificate;
10. calls the canonical `TaskStateStore.finalize_task` only for `COMPLETE` + sufficient evidence.

If an unrelated sibling changes the global mission revision during execution, finalization may rebase
only when the exact task contract is unchanged and still `ACTIVE`. If the task itself changed, the
result is a reconciliation conflict rather than a last-writer-wins overwrite.

## Parent tool contract

`mission_dispatch_next` is intentionally **non-idempotent** and requires `expected_revision`.

The existing execution ledger therefore binds an exact dispatch attempt to the mission revision the
parent just observed. An exact retry can be deduplicated; a subsequent task dispatch requires reading
fresh state and supplying the new revision.

`hierarchical_orchestrate` is read-risk cognition. It cannot grant capabilities or bypass parent policy.

## Worker authority

Cell leads have no tools. Their job is only to propose 2+ materially independent subproblems when the
extra structure is justified.

Leaf workers run through the existing `AgentRuntime` with a reduced tool/scope surface. They cannot gain
persistent-write or external-side-effect authority merely because a parent created a cell.

## What v0.7 does not claim

The reference implementation establishes coordination, budgeting and evidence-gated completion. It does
not yet prove that recursive cells beat a flat sparse team on representative provider workloads.

The next empirical/research step is **independence-aware marginal compute**: buy a second/third attempt
only when it is likely to add a genuinely new reasoning/evidence channel. Raw agent count is not a
quality metric.
