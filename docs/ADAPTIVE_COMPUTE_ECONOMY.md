# Adaptive Compute Economy (v0.4)

## Objective

v0.4 turns team orchestration from a fixed routing heuristic into a persistent local
**value-of-compute controller**. The goal is not to maximize agent count or reasoning
length. The goal is to buy the smallest amount and shape of inference that is expected
to close the remaining quality gap.

The default implementation requires no external backend. It uses SQLite for strategy
outcomes and cache metadata, and deterministic local feature hashing for vector spaces.
Provider/model APIs remain pluggable through the existing LiteLLM layer.

## Decision surface

For each planned work item the compute market can choose:

```text
stop
cheap_single
cheap_pair
primary_single
mixed_pair
```

A pair is not a broadcast debate. It is two independent attempts with different
instructions whose compact outputs are later reconciled by the lead synthesizer.

The controller scores candidates from:

- task profile;
- task difficulty;
- critical/non-critical status;
- current team confidence;
- remaining dollar budget when supplied;
- empirical strategy success for the same profile/difficulty bucket;
- empirical provider-reported cost;
- historical quality gain;
- an explicit exploration bonus while evidence is sparse.

Cold-start priors are transparent configuration, not hidden model knowledge. Once enough
samples exist for a bucket, empirical outcomes can overturn the cold-start policy.

## Why this is different from static cheap/premium routing

A static router asks "which model should answer this task?"

The compute economy asks:

```text
what is the cheapest next computation likely to reduce the remaining uncertainty?
```

That computation can be one cheap rollout, diverse cheap rollouts, one stronger rollout,
a mixed independent pair, a cache reuse, or no additional compute.

This is deliberately closer to test-time resource allocation than ordinary model routing.

Research anchors:

- Adaptive Test-Time Compute Allocation via Constrained Policy Optimization:
  https://arxiv.org/abs/2604.14853
- SeqRoute, global budget-aware sequential routing:
  https://arxiv.org/abs/2605.25424
- R2-Router, joint model and reasoning-budget routing:
  https://arxiv.org/abs/2602.02823
- Scaling Test-time Compute for LLM Agents:
  https://arxiv.org/abs/2506.12928
- Dynamic Coalition Formation and Communication Pricing:
  https://arxiv.org/abs/2608.07532

These papers motivate the optimization problem; v0.4 does not claim to reproduce their
learned policies or theoretical guarantees.

## Persistent outcome market

`ComputeEconomyStore` records per:

```text
(action, profile, difficulty_bucket, critical)
```

- samples;
- successful episodes;
- actual provider-reported cost;
- observed synthesis-confidence gain.

A conservative Beta prior prevents tiny samples from immediately rewriting routing.
The first implementation is intentionally interpretable and bandit-like. A future
offline policy can be trained from this ledger without making a learned router part of
the trust kernel.

## Hard bounds remain outside the learned policy

The adaptive controller may optimize *inside* the allowed envelope, but it cannot expand it.

- `max_agents` caps actual child attempts, including redundant pairs.
- `max_rounds` caps synthesis/follow-up cycles.
- `max_cost_usd` remains a hard parent-accounted ceiling.
- children retain reduced tools and cannot perform normal host writes or external side effects.
- high-impact parent actions retain the existing approval and blind-verification path.

A learned cost policy therefore cannot grant itself more authority.

## Multi-space semantic work cache

v0.3 used one hashed semantic space. v0.4 provides `MultiSpaceSemanticWorkCache` with
separate local representations:

```text
intent      — what the task is trying to achieve
procedure   — audit/debug/search/test/etc. method features
entities    — paths, URLs, IDs, versions and other material targets
full        — complete normalized text
```

Retrieval uses a weighted hybrid score, but semantic **direct** reuse has extra gates:

1. the cached work must already be verified and direct-eligible;
2. the hybrid score must pass the direct threshold;
3. material entity overlap must pass a separate floor;
4. procedure similarity must pass a separate floor when both sides expose procedures;
5. workspace/context fingerprint must match;
6. volatile work remains unable to become semantic direct truth.

A similar procedure involving a different file, endpoint, account, version or other
material target is therefore more likely to become a reference hint than a silent direct hit.

## Cache calibration hook

Semantic thresholds are not assumed to be universally calibrated. v0.4 stores
accept/reject feedback by cache namespace/kind and can automatically tighten the direct
threshold when measured precision falls below the configured target.

The general design is informed by:

- Krites verified semantic caching:
  https://arxiv.org/abs/2602.13165
- Closing the Calibration Gap in Semantic Caching:
  https://arxiv.org/abs/2606.19719
- LaCache robust semantic caching:
  https://arxiv.org/abs/2608.01718

v0.4 remains more conservative than a pure semantic-serving cache because a task result
can carry capabilities and downstream consequences.

## No mandatory vector database

The storage API is deliberately local-first. Qdrant, pgvector or a custom vector-data
backend can replace the local scanner later, but correctness must not depend on installing
one. This matters for three reasons:

- the harness remains usable on a laptop/VPS with near-zero infrastructure cost;
- tests stay deterministic;
- a vector backend remains a performance/indexing component, not a capability authority.

## Current learning boundary

The current strategy outcome signal combines operational success with synthesis confidence
gain. That is useful but not a proof of task correctness.

The next evidence upgrade should feed the market domain-specific verification certificates:

- tests passed for code;
- immutable hashes/signatures for datasets;
- source agreement/freshness for research;
- explicit postcondition checks for operations.

Only then should an offline learned policy be promoted over the transparent market heuristic.

## Evaluation target

A representative held-out workload should compare:

1. single primary;
2. single cheap;
3. fixed broadcast MoA;
4. sparse v0.3;
5. v0.4 adaptive compute without multi-space cache;
6. v0.4 adaptive compute + multi-space cache;
7. future learned policy.

Measure at minimum:

- task success with deterministic evidence where possible;
- total input/output/cached tokens;
- total dollars;
- latency;
- child attempts;
- rounds;
- direct/reference cache hits;
- false direct-reuse rate;
- quality gain per dollar;
- human interventions.

The optimization target is the Pareto frontier, not maximum raw spend or maximum raw agent count.
