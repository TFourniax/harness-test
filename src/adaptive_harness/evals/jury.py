from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from adaptive_harness.providers.base import ModelProvider


@dataclass(slots=True)
class JuryVerdict:
    passed: bool
    score: float
    reasons: list[str]


class IndependentJury:
    """Model-based fallback grader; never supersedes deterministic failures."""

    def __init__(self, provider: ModelProvider, models: list[str], quorum: int | None = None) -> None:
        self.provider = provider
        self.models = models
        self.quorum = quorum or (len(models) // 2 + 1)

    async def grade(self, task: str, candidate: str, rubric: list[str]) -> JuryVerdict:
        async def one(model: str):
            # De-anchoring: ask judge to formulate its own ideal answer/criteria before seeing candidate.
            anchor = await self.provider.complete(
                model=model,
                messages=[
                    {"role": "system", "content": "Derive an ideal solution outline from the task and rubric. Do not score anything yet."},
                    {"role": "user", "content": f"TASK:\n{task}\nRUBRIC:\n- " + "\n- ".join(rubric)},
                ],
                tools=[],
                temperature=0,
                max_tokens=800,
            )
            verdict = await self.provider.complete(
                model=model,
                messages=[
                    {"role": "system", "content": "Using your independently-derived ideal, return JSON only: {\"pass\":bool,\"score\":0..1,\"reason\":\"...\"}."},
                    {"role": "user", "content": f"IDEAL:\n{anchor.content}\n\nCANDIDATE:\n{candidate}"},
                ],
                tools=[],
                temperature=0,
                max_tokens=500,
            )
            try:
                return json.loads(verdict.content or "{}")
            except Exception:
                return {"pass": False, "score": 0.0, "reason": "invalid judge output"}

        votes = await asyncio.gather(*(one(m) for m in self.models))
        pass_count = sum(bool(v.get("pass")) for v in votes)
        score = sum(float(v.get("score", 0)) for v in votes) / max(1, len(votes))
        return JuryVerdict(
            passed=pass_count >= self.quorum,
            score=score,
            reasons=[str(v.get("reason", "")) for v in votes],
        )
