# Governed Self-Improvement

## Objective

The agent may discover weaknesses and author patches to its own prompts, skills, tool descriptions, routing logic, evaluators, policies, and core code. It may **not** silently promote those changes to the live harness.

This distinction gives the system genuine self-development capability without making “the model decided it is safer now” an authorization mechanism.

## Current loop

The **trust kernel is not self-writable** through this loop. Policy, approval/execution ledgers, promotion logic and the trusted eval bank may be proposed for change, but not self-promoted. The evolvable shell includes prompts/context assembly, skills, retrieval, routing, tool interfaces and ordinary runtime strategies.


```text
real run traces
   ↓
observable weakness mining
   ↓
one minimal hypothesis
   ↓
LLM-authored unified diff
   ↓
isolated repository copy
   ↓
patch apply check
   ↓
deterministic regression suite
   ↓
security guard checks
   ↓
proposal JSON artifact
   ↓
HUMAN approve/reject
   ↓
exact approved patch applied to clean Git worktree
```

`SelfImprovementEngine` deliberately asks for **one minimal change tied to one observed weakness**. This limits causal ambiguity: if five subsystems change simultaneously, a benchmark improvement does not reveal which change helped or what silently regressed.

## What should trigger a proposal

Use telemetry, not introspective vibes. Useful signals include:

- repeated evidence-gate blocks;
- repeated tool argument/execution failures;
- verifier rejections;
- step-budget exhaustion;
- repeated human corrections;
- high latency/cost with equal quality;
- weak retrieval/skill selection;
- eval regressions clustered by task type;
- security/adversarial suite failures.

A mature `WeaknessMiner` should cluster failures across many runs and estimate expected value before spending model/eval budget on a candidate.

## Candidate search

Do not use a single greedy lineage forever. Recommended production design:

- stable production champion;
- several candidate branches (“archive”);
- mutations focused on specific failure clusters;
- replay on capability + regression + adversarial suites;
- Pareto comparison on quality, safety, cost, latency;
- stop an evolution lineage when held-out utility saturates or regresses; do not assume later iterations are better;
- promote only candidates that dominate or meet explicit tradeoff policy;
- retain rollback and provenance for every promoted change.

This borrows the useful part of evolutionary self-improvement while refusing autonomous live-code replacement. It also follows the August 2026 evidence that evolution can saturate early: the current champion remains a rollback anchor, and additional mutation is not itself evidence of progress.

## Evaluation contract

A promotion should require all hard gates:

- patch applies cleanly;
- complete regression suite passes;
- targeted capability eval improves or resolves the mined failure;
- adversarial/security suite does not regress;
- no new unauthorized capabilities/scopes;
- cost/latency stay within budget or are explicitly traded for quality;
- independent reviewer/jury does not identify a blocking defect;
- human approves the exact candidate fingerprint.

Then use canary deployment and automatic rollback on post-promotion regressions.

## Judge discovery

The harness should select judges from the **artifact type**, not from a fixed global “critic prompt”:

| Task artifact | Strongest judge first | Secondary |
|---|---|---|
| code patch | tests/type/lint/runtime | independent patch reconstruction |
| research | citation/source coverage + claim entailment | independent researcher/jury |
| scraping | schema/coverage/dedup invariants | sample audits |
| data transform | exact invariants/reconciliation | independent recomputation |
| plan/advice | constraint coverage | multi-model rubric + human |
| external action | exact diff/state precondition | blind verifier + human |
| harness mutation | full regression/security suites | diverse jury + human promotion |

The key is that an LLM judge is used only when there is no stronger executable oracle.

## Human approval should be a protocol, not a chat phrase

Approval is bound to:

- a proposal/action ID;
- an immutable fingerprint;
- the exact diff or tool call;
- eval results;
- time/version context.

If the action changes after approval, the fingerprint changes and approval is invalid.

## Future automatic maintenance

Safe auto-maintenance can be progressively enabled for changes that are both reversible and strongly mechanically verifiable, for example:

- refreshing a generated cache;
- rebuilding a search index;
- updating dependency lock metadata in an isolated branch;
- regenerating tool schema caches;
- pruning stale derived memory.

Core policy, credential access, privilege expansion, external effects, and self-promotion should remain human-governed.


## Separate skill-evolution lane

Successful traces are not automatically “knowledge”. A separate lane distills reusable procedures only from repeated successful runs spanning distinct goals, holds the candidate in quarantine, screens for persistent trigger/bypass patterns, and then requires explicit human approval. This is intentionally conservative because August 2026 trajectory-poisoning work demonstrates that repeated attacker-shaped experience can become a durable malicious skill.

The reference rule (>=3 successful runs, >=2 distinct goals) is a minimum anti-one-shot barrier, **not a proof of benign provenance**. Production should add source/tenant trust domains, signed task origin, adversarial negative cases, and a held-out skill behavior suite before promotion.
