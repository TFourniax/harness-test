# Research Evidence Behind the Design

Snapshot date: 2026-08-16. This is a design evidence log, not a claim that any benchmark directly predicts every real-world deployment.

| Work | Empirical/result signal | Design implication used here | Caveat / challenge |
|---|---|---|---|
| Anthropic, *Building Effective AI Agents* | Recommends simple composable patterns and careful agent-computer interfaces | small core loop, explicit tool contracts | advice/pattern report, not a universal benchmark |
| Anthropic, multi-agent research | Reported strong research gains but ~15× chat token use for multi-agent systems | delegation is conditional and breadth-first | research tasks parallelize better than tightly coupled coding |
| Anthropic, long-running harness work | external progress/state and evaluator separation for long-horizon work | durable checkpoints, trace store, independent verifier | specific application settings |
| Self-Harness, arXiv:2606.09498 | Terminal-Bench-2.0 held-out pass rates: 40.5→61.9, 23.8→38.1, 42.9→57.1 across three model families | weakness mining → minimal patch → regression validation | benchmark/domain limited; autonomous acceptance is too permissive for production root-of-trust changes |
| Darwin Gödel Machine, arXiv:2505.22954 | SWE-bench 20.0→50.0%; full Polyglot 14.2→30.7%; uses archive/open-ended candidate lineages | candidate archive rather than one greedy self-rewrite | coding-specific and expensive; still requires safety precautions |
| MUSE-Autoskill, arXiv:2605.27366 | lifecycle-managed self-created skills beat human-authored skills on the successfully covered SkillsBench subset (85.24% vs 81.17%); transferred MUSE skills reach 51.90% accuracy on Hermes | skills are long-lived, testable procedural assets | reported subset/benchmarks do not imply that autonomous skill creation is uniformly superior; coverage and poisoning remain constraints |
| Hierarchical Self-Improvement, arXiv:2608.08466 | task-specific harnesses are evolved and hot-swapped through a fixed injection seam | keep one stable universal kernel, evolve task-family profiles rather than one monolithic scaffold | very recent preprint; broad production generalization remains unproven |
| Adaptive Auto-Harness, arXiv:2606.01770 | reports sustained gains on open-ended task streams using a harness tree, solve-time routing and human steering | production evolution should route among specialized descendants and avoid dense updates to one global harness | benchmark/task-stream choice may not transfer to every domain |
| PoisonedEvolution, arXiv:2608.05563 | at 10% attacker support, target behaviors embedded in 546/600 SkillClaw trials (91.0%) and 369/600 Trace2Skill trials (61.5%) | experience→skill promotion is a security boundary; quarantine, evidence diversity and human promotion | attack evaluation uses controlled canary behaviors and specific skill pipelines |
| Query-only trajectory backdoors, arXiv:2608.08303 | shows conditional backdoors can be induced through repeated task queries while preserving clean-task utility | never equate repeated trajectory agreement with trustworthiness | very recent preprint; exact transfer rate to this harness is unknown |
| ECLoop, arXiv:2607.28815 | SWE-bench Verified Pass@1 +4.8 to +11.8 pp, token use reduced up to 12.1% | pre-commit evidence gate, not only post-hoc critique | evaluated on coding; evidence compilation must be domain-specific elsewhere |
| RETRACE, arXiv:2608.08950 | mini-SWE-agent +7.0 pp (GPT-5-mini) and +3.6 pp (MiniMax M2.5); also gains on OpenHands | backward reconstruction without original task before semantic reconciliation | current paper is coding-patch focused; generalized here to high-impact actions |
| AgentLens, arXiv:2605.12925 | 10.7% of passing trajectories in its 1,815-trajectory subset are “Lucky Passes” | process/trace quality matters; passing final tests is not enough | process metric itself is another proxy and must not replace ground truth |
| *More Convincing, Not More Correct*, arXiv:2607.05904 | judge pass 0.72→0.94 while true accuracy stayed ~0.20; strict 3-judge ensemble still accepted 55% of manufactured errors; commit-first de-anchoring sharply reduced false positives | deterministic oracles first; judges commit an ideal answer before seeing candidate; jury cannot override deterministic failures | GSM8K/self-play setup does not prove the exact rates transfer to all agent evals |
| HarnessCompass, arXiv:2608.01918 | GPT-5.4 SWE-bench Verified Pass@1 54%→66% in five evolution iterations; argues for constrained evolution, proactive feedback and component-wise optimization | keep mutation scope constrained and evaluate components/held-out transfer instead of allowing unconstrained “improve everything” rewrites | very recent preprint; one coding benchmark/model setting does not establish a universal optimum |
| Evo-Bench, arXiv:2608.09096 | nine-model study reports up to +16.6 absolute improvement, strong domain dependence, transfer across policy models, and early saturation followed by harmful later edits | use harness-sensitive held-out suites, domain profiles, early stopping, champion/archive rollback; never assume more self-evolution is monotonically better | benchmark is itself newly proposed and may not reflect production distributions |
| LongHorizon-Harness, arXiv:2608.01964 | explicit external task state + Manage–Execute–Audit improves multiple long-horizon benchmarks and backends | production design adds a separate environment-backed task-state plane rather than storing progress as free-form context | very recent preprint; domain-specific state verification remains necessary |
| OpenAI Codex harness/safety engineering (2026) | agent loop/control plane owns tools, run state, recovery, approvals; sandbox/network policy is a separate technical boundary | keep authorization/checkpoints/tool routing in harness and computation in constrained execution plane | vendor engineering report, not a universal benchmark |
| MCP spec 2026-07-28 | stateless per-request metadata, `Mcp-Method`/`Mcp-Name`, cacheable lists, tools intended to retain human-denial ability | universal tool adapter with per-request metadata, namespacing, local default-deny policy, remote prose exclusion and schema pinning | interoperability does not establish trust; remote tool descriptions/schemas are themselves untrusted inputs |
| LiteLLM current docs | unified interface across 100+ providers; OpenRouter and Replicate supported; OpenAI-compatible endpoints supported | model-vendor neutral provider boundary | provider feature parity and tool-calling semantics still vary |

## Synthesis

The strongest common pattern is **externalized control**. Capability grants, evidence state, run state, evaluation baselines and promotion authority should not be variables the acting model can simply talk itself into changing.

The second pattern is **empirical evolution with compartmentalization**. Self-improvement is meaningful only when a proposed change survives a test distribution it could not cheaply manipulate. A universal control plane should therefore remain stable while task-family profiles/skills evolve independently. This repository freezes the candidate's baseline tests/evals during isolated evaluation and refuses self-promotion changes to the root-of-trust files.

The third pattern is **tainted experience must not silently become trusted instruction**. Recent trajectory-poisoning results make persistent skill learning a first-class security boundary, not merely a memory optimization. This reference requires repeated cross-goal evidence, quarantine checks and human promotion, while acknowledging that production deployments need stronger provenance and adversarial skill tests.

The fourth pattern is **heterogeneous verification**. The best available oracle depends on the artifact. A test runner is a better judge of executable behavior than a prose critic; source-grounding checks are better for factual research; an LLM jury is reserved for semantic dimensions that remain hard to mechanize.

## Primary links

- https://www.anthropic.com/engineering/building-effective-agents
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/multi-agent-research-system
- https://developers.openai.com/api/docs/guides/agent-evals
- https://developers.openai.com/api/docs/guides/trace-grading
- https://arxiv.org/abs/2606.09498
- https://arxiv.org/abs/2505.22954
- https://arxiv.org/abs/2605.27366
- https://arxiv.org/abs/2608.08466
- https://arxiv.org/abs/2606.01770
- https://arxiv.org/abs/2608.05563
- https://arxiv.org/abs/2608.08303
- https://arxiv.org/abs/2607.28815
- https://arxiv.org/abs/2608.08950
- https://arxiv.org/abs/2605.12925
- https://arxiv.org/abs/2607.05904
- https://arxiv.org/abs/2608.01918
- https://arxiv.org/abs/2608.09096
- https://arxiv.org/abs/2608.01964
- https://openai.com/index/unrolling-the-codex-agent-loop/
- https://openai.com/index/running-codex-safely/
- https://modelcontextprotocol.io/specification/2026-07-28
- https://docs.litellm.ai/docs/providers

## v0.3 — cost-aware multi-agent synthesis

The v0.3 team plane was added after reviewing multi-agent quality gains together with the cost/communication literature. The design deliberately does not copy any one harness.

| Work | Evidence retained | Design consequence | Caveat |
|---|---|---|---|
| Mixture-of-Agents (2024) | Layered multi-model aggregation can substantially improve benchmark quality. | Preserve the idea of multiple independent viewpoints and a lead synthesizer. | Full previous-layer broadcast multiplies context; not used as the default topology. |
| AgentPrune (2024) | Reports 28.1–72.8% token reduction after pruning redundant communication in evaluated MAS topologies. | Treat communication edges as a cost and send only dependency summaries. | Benchmark-specific; not a universal optimal graph proof. |
| AgentDropout (2025) | Reports average 21.6% prompt and 18.4% completion token reductions while dynamically removing redundant agents/edges. | Agents are activated conditionally rather than permanently. | Learned dropout method is not reproduced; v0.3 uses auditable heuristics first. |
| LLMRouter (2026) | Frames routing as sequential quality/cost optimization; learned routers beat fixed baselines in its benchmark. | Store task-family/difficulty/model outcomes and adapt cheap-vs-primary routing from traces. | Current v0.3 router is a small empirical Bayesian rule, not the paper's full framework. |
| Dynamic Coalition Formation (2026) | Models net utility as coalition value minus agent/communication costs and reports strong synthetic results for marginal activation. | Add expected-value/difficulty utility gates and bounded follow-up rounds. | Authors explicitly note main router remains heuristic and synthetic robustness degrades under assumption violations. |
| VectorQ (2025) | Shows one global semantic-cache threshold is inadequate; adaptive regions improve hit/error tradeoff in studied datasets. | Separate strict direct reuse from a lower semantic-reference zone. | v0.3 does not yet learn threshold regions. |
| Krites (2026) | Uses asynchronous verification to expand curated semantic-cache coverage without changing the serving critical path. | Keep “verified/promoted” cache state distinct from ordinary dynamic entries. | v0.3 uses deterministic provenance rules rather than an asynchronous LLM cache judge. |
| Hermes Agent docs (current) | Fresh isolated subagent contexts, parallel delegation, worktree guidance, cross-session prompt caching, and stable prefixes are practical harness patterns. | Preserve fresh bounded child contexts; keep parent prefix stable; plan isolated writable worktrees as the next code-team step. | We intentionally add DAG/value/cache governance rather than assuming every delegated task is worth spawning. |
| OpenRouter prompt/response caching (current) | Provider prompt caches benefit from stable prefixes; OpenRouter also exposes exact response caching. | Move run-dynamic observations out of the stable system prefix and keep application cache orthogonal to provider cache. | Provider behavior/pricing can change and must be measured from actual usage metadata. |

Primary references:

- Mixture-of-Agents — https://arxiv.org/abs/2406.04692
- AgentPrune — https://arxiv.org/abs/2410.02506
- AgentDropout — https://arxiv.org/abs/2503.18891
- LLMRouter — https://arxiv.org/abs/2608.06867
- Dynamic Coalition Formation and Communication Pricing — https://arxiv.org/abs/2608.07532
- VectorQ — https://arxiv.org/abs/2502.03771
- Krites — https://arxiv.org/abs/2602.13165
- Hermes delegation — https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/delegation.md
- Hermes caching/configuration — https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/configuration.md
- Hermes prompt-cache implementation — https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_caching.py
- OpenRouter prompt caching — https://openrouter.ai/docs/guides/best-practices/prompt-caching
- OpenRouter exact response caching — https://openrouter.ai/docs/guides/features/response-caching
