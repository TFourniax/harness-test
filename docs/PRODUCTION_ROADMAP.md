# Production Roadmap

The reference implementation intentionally keeps its core understandable. A production “universal agent” should harden the same contracts rather than replacing them with opaque framework magic.

## P0 — Control-plane hardening

- Postgres for traces/approvals/checkpoints; append-only audit stream.
- Valkey/Redis for queues, leases, deduplication, distributed locks and idempotency keys.
- Object storage for large artifacts and traces.
- OpenTelemetry spans linking model calls, tool calls, approvals, evals and candidate commits.
- external token/time/cost accounting at run, child-run, tool and provider levels (the reference already enforces provider-reported model cost when available).
- provider fallback/routing policy with quality tiers and circuit breakers.
- per-provider privacy/data-retention policy metadata.

## P1 — Execution plane

- microVM sandbox workers for generated code and arbitrary scraping/browser tasks;
- separate read-only source checkout + writable overlay per task;
- egress proxy with domain/IP policy and DNS pinning;
- short-lived secret broker credentials mounted only into the exact tool invocation;
- browser automation through a separately sandboxed MCP service;
- content extraction pipeline with MIME/size limits, malware scanning, canonicalization and provenance.

## P2 — Advanced task-family routing, state, memory and skills

The reference already ships deterministic generic/code/research/scraping/operations/advisory profiles. Production should add:

- separately versioned profile lineages and candidate archives;
- empirical/learned solve-time routing with an explicit fallback to the stable generic profile;
- profile-local held-out eval suites so one domain improvement cannot silently regress another;
- explicit long-horizon task state outside model context, advanced only from environment-backed evidence through a Manage–Execute–Audit-style transition protocol;

- Postgres truth + vector/sparse hybrid retrieval for semantic memory;
- immutable episodic trace store;
- curated procedural playbook store with confidence, source, freshness and supersession links;
- skill-level unit tests, poisoning/adversarial tests and usage/quality telemetry;
- quarantined skill merge/split/deprecate proposals with provenance-diversity requirements;
- memory garbage collection for stale/redundant derived knowledge.

## P3 — Evaluation fabric

Maintain separate suites for:

- coding / repo engineering;
- research / source-grounded synthesis;
- web extraction / scraping quality;
- planning and operations;
- tool-use correctness;
- prompt injection / exfiltration / confused-deputy attacks;
- long-horizon checkpoint/resume;
- cost and latency.

The eval bank should be versioned outside the candidate’s writable workspace, with hidden/held-out cases. Record baselines per model and per harness version.

## P4 — Evolution engine

- cross-run failure clustering;
- expected-value prioritization of weaknesses;
- candidate archive/lineages with early-stopping and rollback when evolution saturates/regresses;
- mutation strategies for prompts, skills, tool interfaces, retrieval, routing and core code;
- parallel isolated candidate evaluation;
- Pareto frontier on quality/safety/cost/latency;
- human review UI showing trace delta + eval delta + exact code diff;
- canary promotion + automatic rollback;
- protected policy/eval files requiring stronger approval tier.

## P5 — Organization / multi-agent scale

v0.3 now provides the local reference for sparse DAG planning, dependency-aware parallel workers, context capsules, targeted follow-up rounds, nested model-cost accounting, semantic work caching, and empirical cheap/primary routing. Production should extend it with:

- distributed leased worker runtime with cancellation and backpressure;
- complete child budgets including model, browser, sandbox, embedding, storage and network cost;
- isolated Git worktree/overlay per code worker, with parent merge/test arbitration;
- learned coalition/communication-edge selection trained on actual traces;
- multi-family independent adversarial reviewers for high-uncertainty claims;
- workspace and tenant isolation;
- durable organization knowledge with policy-scoped hybrid retrieval;
- a pluggable dense+sparse vector backend (Qdrant/pgvector/etc.) for cache and procedural retrieval;
- learned semantic-cache thresholds by task family/freshness/provenance;
- provider-native prompt-cache telemetry and automatic prefix/cache policy optimization;
- controlled exact-response cache use only for deterministic/idempotent prompts;
- benchmark dashboards comparing single-agent, fixed swarm, sparse team, and cached sparse-team Pareto frontiers.

## Definition of “better”

A new harness version is not better because a model says it feels smarter. It is better only if, on a fixed and held-out evaluation distribution, it improves the desired utility while satisfying hard safety constraints and an explicit cost/latency envelope.
