# Independence-Aware Marginal Compute — v0.8

v0.8 changes the question from:

> How many agents can this task use?

into:

> Is the **next real model rollout** likely to add enough new, task-relevant verification to justify
> its marginal dollar/token cost?

This matters because agent count is not evidence. Multiple workers can repeat the same source, share the
same blind spot, or produce different prose from the same underlying information channel.

## No majority vote

The panel does not aggregate by vote and does not pay for a final LLM judge/synthesis call.

Each attempt is blind to peer answers. Selection is deterministic and evidence-first:

```text
VERIFIED > SUPPORTED > UNVERIFIED > REFUTED
then evidence strength
then scope/postcondition coverage
then deterministic evidence
then independent-source count
then certificate score
then successful execution/confidence
then lower cost as a tie-break
```

A minority attempt with a strong deterministic task-bound proof can therefore beat two plausible
unverified answers. Conversely, one deterministic refutation in the combined evidence can prevent the
panel from reporting success even when another attempt sounded convincing.

## Exact rollout accounting

v0.8 introduces a shared atomic `LeafAttemptBudget`.

A panel claims one slot **immediately before each real model call**. It does not reserve three slots just
because `max_panel_attempts=3`.

This gives two important properties:

- strong first proof → one real rollout and one consumed leaf slot;
- concurrent cells/panels cannot collectively hide more model calls than `max_leaf_attempts` permits.

The runner's self-reported attempt count is not authoritative; the shared counter is.

## Blind method lanes

Attempts deliberately use different methods without seeing peer transcripts.

Examples:

- code: postcondition → falsification → invariants;
- research: primary source → disconfirming source → independent source family;
- scraping: schema → adversarial sample → independent extraction;
- operations: direct state → failure/recovery mode → alternate postcondition.

For hard/critical work, the default tier pattern is:

```text
attempt 1: primary
attempt 2: cheap falsifier
attempt 3: primary (only if still justified)
```

This aims to reduce correlated errors without paying premium-model cost for every independent lane.
Medium work usually starts cheap and reaches a primary third lane only when the market still sees value.

If the preferred tier does not fit the remaining escrow, a primary attempt may degrade to cheap. If
even the cheap prior cannot fit, no call is bought.

## Canonical evidence channels

Exact `call_id` values remain the audit trail, but they are a poor measure of independence: two agents
can fetch the same URL twice and receive different call IDs.

Every tool observation therefore receives a locally generated:

```text
provenance_fingerprint = SHA256(canonical(tool_name, arguments))
```

Raw arguments are not copied into observation metadata.

The diversity scorer uses these canonical evidence channels when available and falls back to call IDs
only for backward compatibility. Thus:

```text
agent A: http_get(url=X), call_id=1
agent B: http_get(url=X), call_id=2
```

is one evidence channel, not two.

The local ToolRegistry also prevents a tool-returned metadata payload from overriding reviewed local
`risk`, `source`, or `provenance_fingerprint` fields.

## Independence score

A candidate attempt is compared conservatively against **every** existing attempt in the panel; the
minimum pairwise independence is used.

Current local signal weights:

```text
55% canonical evidence-channel novelty
20% semantic answer novelty
15% deliberate method novelty
10% model/tier novelty
```

Without any new evidence channel, even maximally different prose/method/model remains below 0.5. This
prevents creative disagreement from becoming a proxy for truth.

Diversity itself is **not** the learning reward.

## Marginal Verification Market

For each task bucket and panel slot (2nd attempt, 3rd attempt), SQLite stores observed marginal values:

- independence;
- increase in verification evidence strength;
- increase in certificate score;
- increase in task/postcondition coverage;
- actual provider-reported cost when available;
- whether that attempt was ultimately selected.

The market learns the value of the **Nth attempt**, not the average quality of a whole panel.

A strong existing certificate can stop immediately. Remaining escrow is a hard ceiling. The third
attempt has a lower cold-start prior than the second.

## Conservative uncertainty after learning

Once a bucket reaches the configured sample floor, v0.8 stops acting on the raw mean. It computes a
one-sided 90% lower confidence bound for:

- marginal verification gain;
- measured independence.

The purchase decision uses those lower bounds. A second agent that is spectacular on some runs and
useless on others therefore cannot look as reliable as an agent that adds moderate value consistently.

Empirical marginal cost is also rechecked against the current escrow after learning; historical real
cost can veto a call even when the original cold-start price prior would have fit.

## Cold-start policy

The default `min_utility` is intentionally conservative (`0.120`). One good attempt is preferred until
there is enough structural/verification value to justify another.

Historical evidence can later overturn the prior per task bucket. A repeatedly redundant second lane
will be stopped; a consistently independent high-value lane can remain worth buying.

## Relationship to v0.7

v0.8 does not fork the distributed runtime.

The same v0.7 cells, leases, hierarchical escrows, Mission Dispatcher and durable State Plane are reused.
`v08_cli` injects one optional `panel_leaf_runner` into the hierarchy and dispatcher. With marginal
diversity disabled, those hooks remain `None` and the v0.7 single-leaf behavior is preserved.

This makes benchmark comparisons cleaner: v0.7 vs v0.8 changes the marginal rollout policy rather than
silently changing the rest of the harness.

## Research motivation

Recent multi-agent work suggests that homogeneous scaling can have diminishing returns when errors are
correlated; heterogeneous information channels can matter more than raw agent count. Other work shows
that sharing identical evidence encourages herding and that selection quality can become the bottleneck
once multiple candidate answers exist.

v0.8 turns those observations into an executable policy with explicit accounting and verification,
rather than assuming that disagreement or ensemble size automatically means robustness.

## Non-claims

v0.8 does not yet prove provider-backed cost/quality superiority. In particular, the reference defaults
have not yet been calibrated on a representative held-out cross-domain workload.

A production claim requires matched benchmarks measuring real task success, evidence quality, dollars,
tokens, latency, attempts purchased, provenance-channel novelty and human intervention against at least
single-agent, fixed ensemble, sparse-team, v0.7 single-leaf and v0.8 marginal-panel baselines.
