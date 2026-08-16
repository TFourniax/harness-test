from __future__ import annotations

import json
from collections import Counter

from adaptive_harness.config import ModelRole
from adaptive_harness.contracts import EvalResult, ImprovementProposal
from adaptive_harness.improvement.evaluator import PatchEvaluator
from adaptive_harness.improvement.patch_security import PatchSecurityAnalyzer
from adaptive_harness.providers.base import ModelProvider
from adaptive_harness.runtime.trace_store import TraceStore


class WeaknessMiner:
    """Turns observable trace symptoms into falsifiable weakness statements.

    Single-run mining can react to a concrete failure. Population mining is stricter:
    a symptom must recur across at least two distinct runs before it can spend model/eval
    budget on a self-modification proposal.
    """

    @staticmethod
    def _symptoms(events: list[dict]) -> dict[str, int]:
        kinds = Counter(e["kind"] for e in events)
        tool_failures = sum(
            1
            for e in events
            if e["kind"] in {"tool_observation", "approved_tool_observation"}
            and not e["payload"].get("ok", True)
        )
        verifier_rejects = sum(
            1
            for e in events
            if e["kind"] == "blind_verification"
            and not e["payload"].get("verdict", {}).get("allow", True)
        )
        return {
            "evidence_blocks": kinds["evidence_block"],
            "tool_failures": tool_failures,
            "run_failed": kinds["run_failed"],
            "verifier_rejects": verifier_rejects,
        }

    def mine(self, events: list[dict]) -> list[str]:
        s = self._symptoms(events)
        weaknesses: list[str] = []
        if s["evidence_blocks"] >= 2:
            weaknesses.append("Agent repeatedly attempts commitment before gathering required evidence.")
        if s["tool_failures"] >= 2:
            weaknesses.append(
                f"Repeated tool execution failures ({s['tool_failures']}) indicate poor tool selection or arguments."
            )
        if s["run_failed"]:
            weaknesses.append("Run exhausted its control budget or terminated without satisfying the task.")
        if s["verifier_rejects"]:
            weaknesses.append(
                f"Independent verifier rejected {s['verifier_rejects']} proposed high-impact actions."
            )
        return weaknesses

    def mine_population(self, events: list[dict], min_runs: int = 2) -> list[str]:
        by_run: dict[str, list[dict]] = {}
        for e in events:
            run_id = str(e.get("run_id", ""))
            if run_id:
                by_run.setdefault(run_id, []).append(e)
        if len(by_run) < min_runs:
            return []

        support = Counter()
        totals = Counter()
        for run_events in by_run.values():
            s = self._symptoms(run_events)
            if s["evidence_blocks"]:
                support["evidence"] += 1
                totals["evidence"] += s["evidence_blocks"]
            if s["tool_failures"]:
                support["tool"] += 1
                totals["tool"] += s["tool_failures"]
            if s["run_failed"]:
                support["failed"] += 1
                totals["failed"] += s["run_failed"]
            if s["verifier_rejects"]:
                support["verifier"] += 1
                totals["verifier"] += s["verifier_rejects"]

        weaknesses: list[str] = []
        if support["evidence"] >= min_runs:
            weaknesses.append(
                f"Evidence-gate ordering failures recur across {support['evidence']} runs "
                f"({totals['evidence']} blocks)."
            )
        if support["tool"] >= min_runs:
            weaknesses.append(
                f"Tool execution failures recur across {support['tool']} runs "
                f"({totals['tool']} failures)."
            )
        if support["failed"] >= min_runs:
            weaknesses.append(
                f"Runs fail to satisfy the task across {support['failed']} runs "
                f"({totals['failed']} failed runs)."
            )
        if support["verifier"] >= min_runs:
            weaknesses.append(
                f"Independent verification rejects high-impact actions across "
                f"{support['verifier']} runs ({totals['verifier']} rejects)."
            )
        return weaknesses


class SelfImprovementEngine:
    """Self-Harness / DGM-inspired governed evolution.

    It may *propose* modifications to prompts, tools, skills, policies and core code.
    It cannot promote them. Promotion is a separate human-governed operation after
    deterministic regressions and independent review.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider,
        role: ModelRole,
        trace_store: TraceStore,
        evaluator: PatchEvaluator,
    ) -> None:
        self.provider = provider
        self.role = role
        self.trace_store = trace_store
        self.evaluator = evaluator
        self.miner = WeaknessMiner()

    async def propose_from_run(self, run_id: str) -> ImprovementProposal | None:
        return await self.propose_from_events(self.trace_store.events(run_id))

    async def propose_from_runs(self, run_ids: list[str]) -> ImprovementProposal | None:
        events: list[dict] = []
        for run_id in run_ids:
            for event in self.trace_store.events(run_id):
                events.append({**event, "run_id": run_id})
        return await self.propose_from_events(events, require_population_recurrence=True)

    async def propose_from_events(
        self, events: list[dict], *, require_population_recurrence: bool = False
    ) -> ImprovementProposal | None:
        weaknesses = (
            self.miner.mine_population(events)
            if require_population_recurrence
            else self.miner.mine(events)
        )
        if not weaknesses:
            return None
        weakness = weaknesses[0]
        compact_trace = json.dumps(events[-80:], default=str)[:50_000]
        try:
            turn = await self.provider.complete(
                model=self.role.model,
                messages=[
                {
                    "role": "system",
                    "content": (
                        "You improve an AI agent harness from repeated empirical trace evidence. "
                        "Propose ONE minimal change tied to the observed weakness. "
                        "Do not broaden permissions, alter tests/evals, or remove safety checks. "
                        "Return JSON only with keys: title, hypothesis, target_files (array), "
                        "patch (unified diff)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"WEAKNESS:\n{weakness}\n\nTRACE SAMPLE:\n{compact_trace}",
                },
                ],
                tools=[],
                temperature=max(0.0, min(1.0, self.role.temperature or 0.2)),
                max_tokens=min(self.role.max_tokens, 5000),
                tool_mode=self.role.tool_mode,
                fallbacks=self.role.fallbacks,
                timeout=self.role.timeout,
                num_retries=self.role.num_retries,
            )
        except Exception:
            return None
        if turn.protocol_error:
            return None
        try:
            payload = json.loads((turn.content or "{}").strip())
            proposal = ImprovementProposal(
                title=payload["title"],
                weakness=weakness,
                hypothesis=payload["hypothesis"],
                patch=payload["patch"],
                target_files=payload["target_files"],
            )
        except Exception:
            return None

        proposal.eval_results = self.evaluator.evaluate(proposal.patch)
        proposal.regression_passed = bool(proposal.eval_results) and all(
            r.passed for r in proposal.eval_results
        )
        security = PatchSecurityAnalyzer.analyze(proposal.patch)
        proposal.security_passed = proposal.regression_passed and security.passed
        if security.reasons:
            proposal.eval_results.append(
                EvalResult(
                    name="self_evolution_security_policy",
                    passed=False,
                    score=0.0,
                    details="; ".join(security.reasons),
                    deterministic=True,
                )
            )
        return proposal
