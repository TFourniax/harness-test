# Validation Snapshot

Date: 2026-08-17
Version: **0.5.0**

This document records what the automated build has actually checked. It is intentionally narrower than a production certification or a universal performance claim.

## Current reference suite

The first complete v0.5 source snapshot passed:

- `pytest -q`: **83 passed**;
- `python -m compileall -q src tests`;
- Ruff critical-error checks (`E9,F63,F7,F82`);
- installation and `0.5.0` package/entrypoint resolution;
- deterministic source-manifest generation;
- deterministic ZIP generation;
- extraction into a clean directory;
- the complete **83-test + compile + Ruff** validation again from the extracted ZIP.

The final acceptance run is also required to execute directly from committed source without a staging overlay.

## Existing trust/runtime coverage

The retained suite covers the previously implemented capability and execution boundaries, including:

- capability policy and profile ceilings;
- exact high-impact approval and resume;
- non-idempotent execution ledger;
- evidence gate and blind high-impact verification;
- session persistence;
- tool-schema validation;
- HTTP SSRF/private-destination and redirect handling;
- default-deny MCP import and schema fingerprint checks;
- runtime self-write firewall;
- self-improvement root-of-trust protection;
- isolated regression/security evaluation and human promotion;
- skill quarantine;
- Telegram default-deny access, durable cursor/session identity and cross-session approval protection.

## Sparse team coverage retained

Tests continue to cover:

- zero-token rejection of obviously simple team work;
- sparse dependency-DAG planning;
- concurrent independent workers;
- bounded context capsules instead of broadcast peer transcripts;
- targeted follow-up rounds;
- child-attempt accounting against `max_agents`;
- parent-level nested model-cost accounting;
- cheap/primary routing fallback paths;
- read-only specialist capability with persistent host-write tools removed from child registries.

## v0.4 economic/cache coverage retained

The v0.4 tests remain in the v0.5 suite and cover:

- cold-start compute shape selection;
- cheap single/pair, primary and mixed-pair paths;
- slot-aware pair downgrade;
- high-confidence stop behavior;
- budget pressure;
- stateful routing outcomes;
- multi-space intent/procedure/entity/full cache representation;
- procedural synonym canonicalization;
- entity mismatch demotion to reference-only;
- workspace fingerprint isolation;
- negative cache-feedback threshold tightening.

## v0.5 verification-certificate coverage

New deterministic tests cover:

- strong task-bound deterministic passes;
- task-relevant deterministic failures producing refutation;
- multi-source external support;
- model-only success receiving very weak evidence weight;
- a task-unrelated compile/build check **not** being allowed to certify unrelated research or advisory work;
- verification metadata and evidence references being preserved in reports.

The relevance calculation is deliberately based on the assigned task/profile rather than allowing a verification command's own claim text to declare itself relevant.

## v0.5 evidence tools

Tests cover the verification command allowlist and rejection of arbitrary commands from the deterministic-verification lane.

`verify_workspace_command` is constrained to recognized test/lint/typecheck/compile/build families and runs through the disposable read-only-host sandbox primitive. `source_fetch` keeps retrieved content untrusted while attaching provenance metadata.

Real Docker execution is not exercised by the GitHub-hosted reference suite when the required audited/preinstalled images are unavailable; the policy/registration/classification path is still tested.

## v0.5 confidence calibration

Tests cover:

- shrinking raw model confidence when evidence is weak;
- lifting confidence when strong deterministic evidence is present;
- capping confidence after deterministic refutation;
- failed/unresolved trajectory penalties;
- the labelled calibration store being separate from live self-labelled success.

A raw synthesis confidence value is therefore not treated as a correctness certificate.

## v0.5 evidence-weighted compute market

Tests cover:

- model-only/weak-evidence outcomes having little routing influence;
- repeated strong-evidence failure being able to overturn a cheap strategy;
- evidence mass and success mass persistence;
- day-scale temporal forgetting;
- no meaningless sub-minute numerical decay;
- hard budget semantics: if no compute shape fits remaining budget, the market stops even on critical work;
- actual child/provider cost remaining charged to the parent budget after execution.

## v0.5 adaptive evidence loop

A dedicated test covers the case where raw synthesis confidence is high but the trajectory is weakly evidenced. When slots/round/budget allow, the orchestrator can reopen one bounded verification-focused follow-up instead of treating confidence alone as completion.

## v0.5 policy arena

Tests cover:

- live/shadow observations not being sufficient for promotion;
- paired benchmark trials on matching task keys;
- minimum evidence strength for benchmark contribution;
- quality/cost/pass-rate comparison gates;
- exact comparison fingerprint generation;
- promotion only with the reviewed exact fingerprint;
- stale fingerprint rejection after comparison data changes.

The policy arena can change economic preferences only; it cannot expand capabilities, approvals, agent limits, round limits or cost ceilings.

## v0.5 trust-kernel protection

The self-evolution immutable set now includes the components that decide what counts as verification/evidence and what compute policy may be promoted:

- evidence tools;
- verification engine;
- confidence calibration;
- compute market;
- policy arena;
- benchmark runner;
- v0.5 CLI/control wiring.

These components may be changed by normal reviewed development, but not silently auto-promoted by the harness itself.

## Not yet proven / not live-tested

v0.5 deliberately does **not** claim:

- statistically meaningful dollar/token superiority on representative OpenRouter, Replicate, Anthropic, OpenAI-compatible or other provider workloads;
- universal quality superiority over sparse v0.4, Hermes, broadcast MoA or another public/private harness;
- calibrated real-provider prompt/KV-cache hit rates;
- production semantic-cache false-direct-reuse rate over a representative trace corpus;
- production hostile-code isolation; microVM/gVisor/Firecracker-class controls remain a stronger target than the reference Docker sandbox;
- production-scale Qdrant/pgvector/custom vector backend performance;
- persistent multi-agent code-writing worktrees with merge/rebase arbitration;
- a complete external long-horizon Manage–Execute–Audit task-state plane;
- that source diversity alone proves factual correctness;
- that a passing deterministic command proves claims outside the check's covered scope.

## Next empirical acceptance gate

On held-out real workloads, compare at minimum:

1. single primary agent;
2. single cheap agent;
3. fixed N-agent broadcast MoA;
4. sparse v0.3;
5. adaptive v0.4;
6. v0.5 with semantic direct reuse disabled;
7. full v0.5;
8. challenger policies proposed by the policy arena.

Use domain-grounded deterministic/oracle evidence where possible. Track task success, evidence strength, input/output/cached tokens, dollars, latency, child attempts, rounds, cache direct/reference hits, false direct-reuse, quality gain per dollar and human intervention.

Keep held-out benchmark truth outside candidate-writable boundaries. A future policy becomes champion only through reproducible paired evidence and an exact reviewed promotion fingerprint.