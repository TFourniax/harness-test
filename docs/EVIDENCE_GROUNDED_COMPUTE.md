# Evidence-Grounded Compute Economy

Version: v0.5.0

## Why this layer exists

A cost-aware multi-agent system can still learn the wrong lesson if its reward is “the worker returned success” or “the synthesizer sounded confident.” Both are easy to miscalibrate and can reward more convincing rather than more correct trajectories.

v0.5 therefore separates four objects:

```text
worker trajectory
      │
      ▼
verification certificate
      │
      ▼
trajectory-calibrated confidence
      │
      ▼
evidence-weighted compute-market update
      │
      ├── live champion execution
      └── challenger diagnostics / held-out benchmark
                     │
                     ▼
             exact reviewed fingerprint
                     │
                     ▼
               policy promotion
```

No object in this chain grants capabilities. Capability and approval policy remain separate and higher priority.

## 1. Verification certificates

`VerificationEngine` converts concrete worker observations into one bounded certificate. The possible verdicts are:

- `VERIFIED`: strong task-bound deterministic postcondition;
- `SUPPORTED`: useful evidence that does not fully close the task;
- `UNVERIFIED`: the model completed without enough environment-backed evidence;
- `REFUTED`: a sufficiently task-relevant deterministic postcondition failed.

The certificate contains:

- evidence strength;
- quality/support score;
- deterministic flag;
- estimated scope coverage;
- number of independent source hosts;
- check descriptions;
- observation references;
- human-readable reasons.

### Postcondition scope matters

Passing a test is not universally meaningful. The verifier estimates whether the test category is actually related to the assigned task.

Examples:

```text
TASK: fix failing parser tests
CHECK: pytest tests/test_parser.py
→ high scope coverage

TASK: research competitor pricing
CHECK: python -m compileall src
→ low scope coverage; cannot certify the research conclusion
```

The check's own description cannot make itself relevant. Relevance is derived from the assigned task/profile, preventing “verification laundering.”

## 2. Evidence-producing tools

### `verify_workspace_command`

A constrained deterministic lane for test/lint/typecheck/compile/build commands. It runs in the existing disposable Docker copy with the host workspace mounted read-only.

Recognized command families include pytest, compileall, Ruff, mypy, pyright, common JS package-manager test/lint/build commands, Cargo checks, Go tests and selected Make targets.

Arbitrary shell commands belong to the normal diagnostic sandbox and are not automatically labelled verification evidence.

### `source_fetch`

Retrieves a public HTTP(S) source while recording provenance metadata such as final host/URL and content hash. Redirects are revalidated through the existing SSRF boundary.

The content remains `UNTRUSTED_EXTERNAL`. Two or three distinct hosts can strengthen support, but source count is not treated as truth by itself.

## 3. Trajectory confidence calibration

`TrajectoryConfidenceCalibrator` does not trust a final model confidence number in isolation.

It combines:

- certificate strength;
- deterministic verification/refutation;
- failed worker fraction;
- unresolved synthesis items;
- optional externally labelled historical reliability.

When evidence is weak, raw confidence is shrunk toward uncertainty and capped. A high raw score can therefore still justify one targeted verification follow-up.

The calibration database is intended for labelled outcomes. The live runtime does not self-label ground truth into that table.

## 4. Evidence-weighted compute learning

The compute market keeps backward-compatible v0.4 operational stats and a new evidence-weighted table.

Per strategy/task bucket it tracks approximately:

```text
effective sample mass
evidence mass
success mass
actual provider-reported cost
verified/calibrated quality gain
last update time
```

An unverified model-only success has small evidence weight. A strong certificate has substantially larger influence.

Observations decay with time using a configurable half-life so a model/provider that changes price or quality does not dominate forever. Sub-minute decay is deliberately ignored because it has no economic meaning at a day-scale half-life and creates numerical drift.

## 5. Hard budget semantics

Expected strategy cost is filtered against remaining budget before execution. If no candidate fits, the market returns `stop` — including for a critical task.

After execution, actual provider-reported costs still flow to the parent run budget, so a provider charging more than estimated cannot silently escape the hard run ceiling.

The economic controller may choose less compute. It may not increase:

- allowed scopes;
- tool risk ceilings;
- approvals;
- `max_agents`;
- `max_rounds`;
- user/operator cost ceiling.

## 6. Champion/challenger arena

A compute policy is a small overlay on the market's decision economics, not a new permission set.

The arena distinguishes data sources:

- `live`;
- `shadow`;
- `matched_action`;
- `benchmark`.

Live/shadow data can diagnose a challenger but cannot promote it. Promotion eligibility requires enough **paired benchmark trials on the same task keys** above the configured evidence floor.

The comparison checks quality, cost and pass-rate safety. A challenger may win through meaningful quality gain without excessive cost, or material cost reduction without quality regression.

### Exact promotion fingerprint

A policy comparison is fingerprinted over the exact champion/challenger identities and measured statistics. Human review approves that fingerprint, not a vague policy name.

If benchmark data changes after review, recomputing the comparison changes the fingerprint and the old approval no longer applies.

This is the compute-policy equivalent of exact action approval.

## 7. Shadow policies

The live champion's bid is authoritative inside the economic layer. Challenger policies may compute alternative bids without adding model calls. These counterfactual recommendations can be logged for analysis.

Shadow disagreement is not proof that the challenger is better. It becomes useful evaluation input only when paired with actual held-out benchmark outcomes.

## 8. Cache interaction

Semantic direct reuse now requires stronger evidence than ordinary reference retrieval. A work item that lacks a sufficiently strong certificate is demoted to a reference hint even if text/vector similarity is high.

This creates a three-way distinction:

```text
retrieval similarity      “this looks related”
verification certificate  “this prior result had evidence”
context fingerprint       “the relevant environment is compatible”
```

Only their conjunction can support direct reuse.

## 9. Trust-kernel placement

The verification, confidence, compute-market, policy-arena, benchmark and evidence-tool modules are explicitly protected from automatic self-evolution.

The self-improver may propose improvements around task-solving components, but it cannot automatically lower the criteria by which success, evidence or policy promotion are judged.

## 10. What remains empirical

The architecture is testable, not self-proving. The next empirical gate should compare, on the same held-out workloads:

1. single primary;
2. single cheap;
3. broadcast MoA;
4. sparse v0.3;
5. adaptive v0.4;
6. v0.5 without direct semantic reuse;
7. full v0.5;
8. any challenger compute policy.

Measure domain-grounded success first, then cost, latency, tokens, child attempts, rounds, cache errors and human interventions. A challenger should not become champion because it merely produces more confident text.