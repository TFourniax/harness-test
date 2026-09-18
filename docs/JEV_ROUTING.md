# JEV decision routing — experimental integration, 2026-09-18

This change is based on `agent/import-harness-telegram` at
`3e21337162602e2593586d8b9238807a973861b0`, not on the almost-empty `main`.
It does not merge or qualify the original draft PR as a production release.

## What is actually integrated

The installed `adaptive-harness` command now runs the v0.8 builder. One bounded
`JevDecisionEngine` is shared by the hierarchical planner and, when explicitly enabled,
the panel used by both hierarchy and mission dispatch. No new dependency is needed:
the client uses the project's existing `httpx` dependency and the documented typed HTTP API.

The default use is a **semantic pre-planner gate**. After existing free structural gates,
JEV chooses `direct_leaf` or `decompose`. A sufficiently confident `direct_leaf` avoids
one generative planner call; the original governed worker still has to do the work.
`decompose`, abstention and failures retain the existing planner. JEV does not generate
child tasks. Critical work keeps the original planner without a JEV request.

Optional panel routing chooses a model tier and/or unused local verification method in
one batched request. These choices were already deterministic and free in v0.8, so this
option is **off by default**. Its overhead must earn its place in an actual evaluation.
The marginal market runs before the request and can veto a more expensive proposal again
afterward. The first method lane remains direct; critical lanes and hard/critical model
routing cannot be downgraded. Peer answers and observations are never included in its state.

Flow:

```text
existing local gates / permissions / budgets
    -> optional JEV choice among fixed local candidates
    -> strict protocol + confidence + probability + margin checks
    -> shadow observation OR accepted choice OR original fallback
    -> budget recheck / original marginal-market veto / atomic rollout claim
    -> original worker, tool guards, verifier and evidence-first selection
```

JEV is not an approval authority, verifier, executor, or completion certificate. It cannot
add tools, commands, scopes, child tasks, money, rollout slots or leases. The new module
is inside the immutable self-evolution boundary. This is a bounded routing integration,
not a replacement for the complete harness or its generative problem-solving models.

## Activation

Install from this branch and copy `config/harness.example.yaml` to your local configuration.
Keep your existing primary/cheap provider configuration and credentials.

```sh
python -m pip install -e '.[dev]'
adaptive-harness jev-status --config config/harness.yaml
```

Add the following to the local YAML. Both `distributed_reasoning.enabled` and
`marginal_diversity.enabled` must remain true. Invalid/orphan JEV configuration is rejected.

```yaml
jev:
  mode: shadow             # off: no JEV; shadow: observe; active: apply accepted choices
  allow_remote_state: true # explicit permission to send the bounded task state to TypeSafe
  model: jev-1.13.0        # version pin; moving aliases deliberately rejected
  api_key_env: TYPESAFE_API_KEY
  planner_gate: true
  panel_routing: false     # optional experiment, not an assumed optimization
  timeout_seconds: 1.5
  min_confidence: 0.80
  min_probability: 0.85
  min_margin: 0.25
  max_calls: 64
  max_total_cost_usd: 0.03
```

Set `TYPESAFE_API_KEY` using your local secret manager or environment; never commit its
value. `jev-status` only shows the environment variable's name and whether it is present.
It does not contact the API, verify the key, or expose its value. Missing keys cause free
local fallback. Move `mode` to `active` only after checking your own workloads. Returning
to `off` restores the original orchestration choices and disables JEV networking entirely.

`shadow` is **not** free: it sends requests and charges them to the same decision budget,
while preserving local choices. It can consume enough budget to reduce later work; the
comparison must account for that. It does not silently authorize transmission when
`allow_remote_state` is false.

## Privacy, failure handling and spend accounting

The endpoint is fixed to `https://api.typesafe.ai/v1/systemone`; TLS verification stays on,
redirects and environment-derived HTTP proxies are disabled, and no retries are made.
Timeout, overload, rate limit, invalid JSON, duplicate fields, nonfinite numbers, missing
usage, unlisted candidates, inconsistent probabilities and model mismatch all retain local
choices. Authentication failures, repeated transport/protocol failures and reported cost
overruns open a process-local circuit breaker.

Only task text, its success criteria/constraints and a small typed routing state are sent.
This is **data minimization, not automatic secret redaction**: secrets embedded in the task
would still be sent after consent. Verify your workload and the provider's data terms.
Trace events contain a hash, local labels, valid bounded probabilities/confidences, model,
reason, timing and cost, not the state, API key, raw answer text or HTTP exception body.
Audit-write failure prevents applying the model proposal. Ordinary pre-existing harness
traces have their own retention/privacy properties; this change does not rewrite them.

Calls reserve $0.003 each by default before dispatch. A well-formed response settles at
reported input tokens times the configured tariff ($0.042/M at the checked date). Unknown
or invalid usage keeps the reservation; parsed larger usage is never silently discarded.
Reported cost above reservation opens the circuit. Decision expenses are included in
planner/panel outcomes and normal escrow settlement, including a panel that loses the last
rollout slot during its decision. Reservations/call caps are shared across concurrent cells
for the **engine/runtime lifetime**, not persisted across restarts or shared across processes.

These are admission controls and conservative local accounting, **not a provider-enforced
billing ceiling**. Usage and prices come from the provider/configuration; an overrun can
already have been billed before it is detected. Unexpected cancellation retains the local
engine's conservative charge/audit event; reconciliation of abruptly interrupted mission
escrows and unknown worker-provider bills remains a broader harness limitation. Existing
model calls can also cost more than their cold-start estimates. Never treat a green test
suite as proof of a hard cap on an external provider's invoice.

## Validation and baseline repairs

An independent local run of the original source produced **166 passing tests, 4 failures**.
The earlier PR description claiming 170 passing tests was not an exact-source qualification.
This integration repairs, rather than suppresses, the failures:

- Package/version/console wiring were still v0.7 although the v0.8 release test expected v0.8.
  The current v0.8 pin is retained; the legacy v0.7 test now checks import compatibility
  instead of requiring two contradictory current package versions.
- Marginal-diversity history lacked its tested lower-confidence-bound fields and still used
  raw means. Approximate one-sided 90% normal bounds are now computed and used. They are not
  distribution-free statistical guarantees; small-sample/unseen-task calibration is unproven.
- Learned empirical call cost could exceed remaining escrow after the first budget check.
  A second affordability check now prevents purchasing that rollout.

Current local source: **239 tests pass** (170 existing/compatibility plus 69 new JEV tests),
and Python compilation passes. The dedicated read-only GitHub Actions workflow tests Python
3.11, 3.12 and 3.13, checks critical Ruff errors and archives its exact source. Check the PR's
exact-head run for its final remote status; this document does not assert an unobserved CI pass.
Historic `artifacts/*0.7.0*` archives remain historical; no new v0.8 binary release is claimed.

New tests cover HTTP wire shape, pinning, strict parsing, abstention, all confidence gates,
missing consent/key, cancellation, concurrency, circuit breaking, audit privacy/failure,
spend admission, planner skipping/fallback, legacy behavior, actual hierarchy settlement,
model-tier restrictions, unused lanes, market vetoes, exhausted rollout slots and dispatch wiring.
Transport is mocked in these tests: **no live JEV request or real-provider performance
measurement has been made for this qualification**.

## Reproducible empirical next gate

```sh
python -m pytest -q
python scripts/eval_jev_routing.py
# Explicitly permits sending the fixture state and charging the supplied API key:
python scripts/eval_jev_routing.py --live --budget-usd 0.03
```

The included 12 English/French cases are synthetic developer-labelled smoke examples,
including dependent-step and instruction-injection boundaries; they are not independent
held-out benchmark evidence. Offline mode only validates the fixture and reports zero
calls, no quality measurement and no latency measurement. Live mode reports coverage,
accuracy on confident decisions, false-direct choices, latency, costs and every fallback.
Abstentions/failures are visible rather than silently excluded from overall coverage.
Replace the fixture with held-out, labelled production-representative cases before tuning.

A routing-only score does not prove the harness is better. The actual promotion experiment
must compare the unchanged free policy against planner-gate-only and gate-plus-panel modes
on matched tasks, fixed provider versions and equal total budgets. Measure task-grounded
success, verification strength/coverage, actual avoided planner calls, total dollars/tokens,
end-to-end p50/p95 latency (including TLS/setup/fallback), interventions and critical failures.
Evaluate French separately. Keep panel routing disabled unless its extra overhead is justified.
No superiority over a strong single agent, Hermes or the original harness is claimed.

## Primary references checked 2026-09-18

- TypeSafe API contract: https://docs.typesafe.ai/api
- Model versions, input-token pricing and language/data caveats: https://docs.typesafe.ai/models

This integration uses the hosted typed-decision API, not an assumed downloadable JEV model,
not a generative chat endpoint, and not an asserted trained local decision tree.
