# Architecture

## Design thesis

A general-purpose agent should not be a giant prompt with every tool attached. The harness should be the **control plane** around a replaceable model: it owns state, capabilities, provenance, evidence, verification, tool contracts, budgets, evaluation, and evolution.

The core design rule is:

> **Models propose; the harness constrains, executes, records, verifies, and governs.**

The strongest “universal” architecture is therefore **not one universal mutable brain**. It is a small universal **trust kernel** plus evolvable task-family profiles (coding, research, scraping, operations, advisory, etc.). The kernel owns authorization, ledgers, eval integrity and promotion; task profiles own changeable strategies, skills, retrieval, model routing and domain evidence rules.

## Layered architecture

| Layer | Responsibility | Must remain outside model control |
|---|---|---|
| Goal contract | objective, success criteria, constraints, budgets | immutable user/system constraints |
| Task/profile router | select a task-family harness profile with generic fallback | kernel capability ceiling and profile signatures |
| Context compiler | smallest relevant context, skills, memory, observations | trust labels and precedence |
| Model gateway | provider normalization and role routing | credentials, provider policy |
| Tool plane | typed tool contracts, MCP/builtin adapters | scopes and risk classification |
| Execution policy | default-deny capabilities, approval rules | authorization decision |
| Evidence plane | pre-commit requirements and evidence references | gate state transition rules |
| Verification plane | backward/blind reconstruction, deterministic tests, jury | deterministic failure cannot be overruled by an LLM |
| State plane | trace, checkpoints, approvals, memory | approval fingerprint/history |
| Evaluation plane | capability, regression, security, cost, latency | baseline and held-out suites |
| Evolution plane | weakness mining and candidate patch generation | promotion to live code |
| Governance | human decisions, Git history, rollback | final high-impact promotion |

## One kernel, many task profiles

Recent auto-harness work strengthens a design we already prefer operationally: evolve domain-specific scaffolds without allowing every task to mutate one global harness. A coding profile may optimize patch verification and test selection; a research profile may optimize source triangulation and citation entailment; a scraping profile may optimize browser/extraction/deduplication. All still pass through the same capability policy, approval ledger, execution ledger and promotion gate.

The reference implementation now includes deterministic solve-time routing across generic, coding, research, scraping, operations and advisory profiles. Profiles can reduce tool/risk surfaces and choose a model tier, but cannot expand the kernel capability ceiling. A production system should go further with versioned profile lineages, held-out profile-local evals and a learned/empirical router that can always fall back to the stable generic profile.

## Why not “multi-agent everywhere”

Multi-agent systems are useful when work is genuinely parallel: web research across independent subquestions, large evidence collection, independent critiques, or heterogeneous specialist tasks. They are often a poor default for code modification or workflows with tightly shared mutable state.

The harness therefore uses a **single principal actor by default** and makes delegation an explicit tool. The delegator receives only bounded subgoals and should be used for breadth-first independent work. This preserves context, reduces token multiplication, and makes causality/evaluation easier.

## Context is a cache, not a database

The live prompt contains only:

- immutable task contract;
- relevant tool contracts;
- a small set of recent observations;
- retrieved procedural skills;
- compact working notes;
- selected procedural memory.

Full trajectories live in the trace store. Durable knowledge lives in memory/skills. Large corpora belong in retrieval systems. The harness should prefer a clean handoff/checkpoint over repeatedly compressing an ever-growing conversation.

## Evidence before commitment

A “commitment” is any action that changes persistent state or creates an external effect. Evidence requirements can be attached to such actions. The model can request actions, but the execution layer refuses to cross the gate until requirements are satisfied.

For a mature deployment, evidence requirements should be task-compiled and mapped to trusted observations, e.g.:

- code write → relevant file inspection + baseline tests;
- infrastructure change → current state + plan/diff + rollback evidence;
- factual report → primary-source evidence and citation coverage;
- outbound communication → recipient/context validation;
- financial/external mutation → current authoritative state + exact proposed delta.

The reference implementation provides the deterministic gate primitives; domain adapters should provide stronger evidence semantics.

## Independent verification

High-impact actions use a backward-check pattern:

1. give the proposed action to an independent verifier **without the original goal**;
2. ask it to infer what task the action appears to accomplish;
3. give that inferred goal and the actual goal to a separate comparison pass;
4. reject scope expansion or mismatch;
5. only then reach the human approval boundary.

This makes the verifier less anchored to the actor’s story about why its action is correct.

## Long-horizon state plane

Long tasks should not rely on an ever-growing transcript as the source of truth. The reference runtime already persists checkpoints, execution ledgers, bounded observations and session memory, but a production deployment should add an explicit **task-state plane** inspired by the 2026 Manage–Execute–Audit results:

- manager state lives outside the model context;
- state changes cite environment observations, not model self-claims;
- execution workers can start from fresh bounded context;
- read-only/domain-specific auditors verify the resulting external state before progress is committed;
- failed or ambiguous transitions remain unresolved instead of being summarized away.

This is deliberately described as a production extension because “verified state” requires domain-specific oracles: tests and repository state for code, authoritative sources for research, schema/reconciliation checks for data, and explicit API state for operations. A generic LLM verdict is not enough.

## Evaluation hierarchy

A candidate is judged in this order:

1. **Deterministic execution evidence** — tests, schemas, type checks, invariants, exit codes, exact state checks.
2. **Independent reconstruction/checks** — derived from artifacts/trajectory, not the actor’s explanation.
3. **Model jury** — only for qualities that cannot be fully mechanized; judges derive their own ideal answer before seeing the candidate.
4. **Human review** — required for harness promotion and high-impact actions.

A higher layer cannot erase a failure from a lower deterministic layer.

## Self-evolution as controlled search

The self-improver treats the harness as a search space, not as mutable identity:

- mine recurring trace symptoms;
- state one falsifiable weakness;
- propose one minimal change;
- create a candidate branch/copy;
- evaluate against baseline + held-out regressions;
- reject safety broadening;
- request human approval;
- promote through Git with rollback.

A production variant should maintain an archive of diverse winning candidate lineages rather than greedily replacing the current harness after each apparent improvement. It should route by task family and preserve the stable kernel. That combines the useful DGM/open-ended-search lesson with 2026 task-specific/Adaptive Auto-Harness results while retaining explicit human governance.

## Tool interoperability

MCP is the preferred universal tool boundary, but **protocol interoperability is not trust**. The local harness independently decides:

- whether a remote tool is exposed at all;
- its risk class;
- its required scopes;
- whether human approval is mandatory;
- whether its output is trusted, which for external MCP is normally “untrusted data”.

The current MCP adapter targets the 2026-07-28 stateless request model and prefixes imported tool names to prevent cross-server collisions.

## Research claims worth retaining — and what we challenge

### Retain

- Simple composable agent loops are easier to evaluate and often outperform overly abstract agent frameworks.
- Tool/interface design materially changes agent performance.
- Long-horizon tasks need durable external state and structured handoffs.
- Reusable skills/playbooks can compound performance across tasks, but experience→skill promotion is itself a trust boundary.
- Pre-commit evidence and independent reconstruction improve reliability.
- Self-improvement can work when changes are empirically evaluated.

### Challenge

- **“Reflection” as evidence:** a model explaining why it is correct is not a reliable verifier.
- **Passing final tests as process quality:** lucky passes and brittle trajectories exist.
- **Single LLM-as-a-judge:** judge bias and reward hacking make it an advisory signal, not a root of trust.
- **More agents = more intelligence:** parallelism can improve breadth, but it multiplies tokens and coordination failure.
- **Memory = save the transcript:** raw history creates retrieval noise and context rot; procedural memory should be curated and provenance-aware.
- **Repeated experience = trusted skill:** August 2026 trajectory-poisoning results show recurrent attacker-shaped traces can implant persistent behavior; evolved skills need quarantine and independent provenance.
- **Autonomous self-edit = self-improvement:** without held-out regression, security gates and rollback it is merely uncontrolled mutation.

## Key public research anchors

- Anthropic — Building Effective AI Agents; Effective harnesses for long-running agents; Multi-agent research system; Context engineering; Writing effective tools.
- OpenAI — Agent evals; Trace grading; Evaluation best practices.
- Self-Harness (2026), arXiv:2606.09498.
- Darwin Gödel Machine (2025/2026 line of work).
- MUSE-Autoskill (2026).
- Hierarchical Self-Improvement / task-specific evolvable harnesses (2026), arXiv:2608.08466.
- Adaptive Auto-Harness (2026), arXiv:2606.01770.
- Trajectory poisoning / query-only backdoors in self-evolving skills (2026), arXiv:2608.05563 and 2608.08303.
- Agentic Context Engineering (ACE).
- ECLoop (2026), evidence-conditioned execution.
- RETRACE (2026), arXiv:2608.08950.
- AgentLens (2026) on trajectory quality / lucky passes.
- MCP Specification 2026-07-28.
