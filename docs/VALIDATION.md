# Validation Snapshot

Date: 2026-08-17
Version: **0.4.0**

This file records what was actually checked in the GitHub Actions build environment. It is intentionally narrower than a production certification or a universal performance claim.

## Passed in the current v0.4 build

- `pytest -q`: **65 passed** after the v0.4 cache-semantics correction.
- `python -m compileall -q src tests`: passed.
- Ruff critical-error checks (`E9,F63,F7,F82`): passed.
- Package installation and 0.4.0 console-entrypoint resolution: passed.
- The deterministic package ZIP is extracted into a clean directory and the complete pytest/compile/Ruff validation is executed again from the extracted archive before the snapshot is accepted.
- CLI imports and existing command registration remain covered by the package/import validation path.
- Capability policy, exact action approval/resume, non-idempotent execution ledger, evidence gate, profile ceilings, tool-schema validation, and session persistence remain covered.
- HTTP SSRF defenses cover private/local destinations and redirect behavior.
- MCP tests cover stateless request metadata, default-deny tool import, local policy mapping, schema fingerprint drift rejection, and external-output trust labeling.
- Self-write firewall, root-of-trust patch protection, regression evaluation, promotion gate, recurring weakness mining, and skill-evolution quarantine remain covered.

## v0.4 Adaptive Compute Economy validation

Deterministic tests cover:

- cold-start compute allocation by task difficulty and criticality;
- easy non-critical work selecting a cheap single attempt;
- medium work selecting two deliberately independent cheap attempts when slots allow;
- hard non-critical work escalating to a primary attempt;
- high-difficulty critical work selecting a mixed cheap + primary independent pair;
- actual child-attempt accounting, so redundant pairs consume two `max_agents` slots;
- remaining-slot downgrade from a pair strategy to an appropriate single strategy;
- budget pressure excluding estimated over-budget compute shapes;
- high-confidence/low-marginal-gain stopping before another worker call;
- persistent empirical strategy outcomes changing later compute decisions;
- provider-reported child/model cost propagation through team metadata and into the parent budget path;
- targeted bounded team rounds remaining compatible with the existing sparse DAG/synthesis loop.

The compute controller is therefore executable and stateful, not only an architectural document. Its current success signal still uses operational success plus synthesis-confidence gain; that is **not** equivalent to a domain-grounded correctness certificate.

## v0.4 multi-space cache validation

Tests cover separate intent/procedure/entity/full representations and the guarded direct-reuse path:

- exact verified work remains reusable inside the same workspace fingerprint;
- a semantically similar task aimed at different material entities is demoted to a reference hint rather than direct reuse;
- close paraphrases with the same material target can reuse verified work when all direct gates pass;
- procedure synonyms such as audit/review/inspect are canonicalized into procedural families instead of weakening the similarity threshold;
- workspace fingerprints continue to isolate cache state;
- negative reuse feedback can tighten the direct semantic threshold after the calibration sample floor;
- unverified and volatile work retain the conservative v0.3 reference-only/direct-reuse restrictions.

The local multi-space implementation uses SQLite and deterministic feature hashing. No external vector database or embedding API is required for the validated path.

## Team runtime validation retained from v0.3

Tests continue to cover:

- sparse team planning into a dependency DAG;
- independent parallel workers;
- compact context capsules rather than broadcast peer transcripts;
- targeted second-round follow-up after synthesis finds a material gap;
- zero-token team rejection for obviously simple work;
- context-scoped exact cache reuse;
- empirical cheap/primary routing adaptation when the v0.4 economy is disabled;
- read-only specialist execution as a registered capability, while host-write tools are removed from child registries.

## Channel validation retained from v0.2

The Telegram/channel layer is covered by deterministic tests for:

- default-deny user authorization;
- optional chat allowlisting;
- private-chat defaults;
- callback payload size and message chunking;
- stable per-human/per-conversation session identity;
- durable update cursor storage;
- exact action fingerprint approvals;
- prevention of cross-session approvals.

No live Telegram token was used in the validation environment.

## Not live-tested / not yet proven

The following claims are deliberately **not** made by v0.4:

- no representative real-provider benchmark yet proves dollar/token savings on OpenRouter, Replicate, Anthropic, OpenAI-compatible endpoints, or other LiteLLM providers;
- no statistically meaningful held-out benchmark yet proves that v0.4 beats sparse v0.3 or fixed broadcast MoA on a real cross-domain workload;
- real provider prompt/KV cache hit rates and cached-token accounting have not yet been measured;
- semantic-cache false-direct-reuse rate has not yet been measured over a representative production trace corpus;
- the compute market has not yet been trained/replaced by an offline learned policy, and no such learned policy is trusted by default;
- synthesis confidence is not treated as a proof of correctness; future market updates should prefer domain-specific verification certificates;
- real Docker specialist execution is not exercised in GitHub-hosted CI; production hostile-code execution should use stronger microVM/gVisor/Firecracker-class isolation;
- production network egress enforcement still requires infrastructure controls in addition to application SSRF defenses;
- real external side-effect adapters such as email, payments, CRM, browser or cloud writes require staging tests and explicit local policy;
- multi-agent persistent code writes using isolated Git worktrees/overlays plus merge arbitration are not yet implemented;
- production-scale Qdrant/pgvector/custom vector backends are not required and not benchmarked;
- the complete external long-horizon Manage–Execute–Audit task-state plane remains a production-roadmap item.

## Production acceptance condition

Before calling a deployment production-ready, benchmark the same harness/model combinations on held-out task suites for coding, source-grounded research, scraping, operations, advisory work, prompt injection, tool poisoning, long-horizon recovery, cost, latency and cache safety.

At minimum compare:

1. single primary agent;
2. single cheap agent;
3. fixed N-agent broadcast MoA;
4. sparse v0.3 orchestration;
5. v0.4 adaptive compute with semantic direct reuse disabled;
6. v0.4 adaptive compute + multi-space cache;
7. any future learned compute policy.

Track task success using deterministic/domain evidence where possible, total input/output/cached tokens, dollars, latency, child attempts, rounds, direct/reference cache hits, false direct-reuse rate, quality gain per dollar and human interventions.

Keep the held-out eval bank outside candidate writable boundaries. A new routing/evolution policy should become champion only with reproducible evidence and a rollback path; adaptive economics never override capability or human-approval boundaries.
