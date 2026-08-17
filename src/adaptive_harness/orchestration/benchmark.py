from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from adaptive_harness.orchestration.policy_arena import ComputePolicy, PolicyArenaStore


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    payload: object


@dataclass(frozen=True)
class BenchmarkOutcome:
    quality: float
    cost_usd: float
    evidence_strength: float
    passed: bool
    latency_ms: float = 0.0


BenchmarkExecutor = Callable[[ComputePolicy, BenchmarkCase], Awaitable[BenchmarkOutcome]]


class PolicyBenchmarkRunner:
    """Run matched held-out cases for champion/challenger compute policies.

    The runner is intentionally provider-agnostic. Production can supply real agent tasks; tests can
    supply deterministic executors. Only outcomes explicitly recorded through this matched benchmark
    path are eligible for policy-arena promotion recommendations.
    """

    def __init__(self, arena: PolicyArenaStore, *, max_parallel: int = 2) -> None:
        self.arena = arena
        self.max_parallel = max(1, int(max_parallel))

    async def run(
        self,
        cases: Iterable[BenchmarkCase],
        executor: BenchmarkExecutor,
        *,
        include_challengers: bool = True,
    ) -> dict[str, int]:
        cases = list(cases)
        policies = [self.arena.champion()]
        if include_challengers:
            policies.extend(self.arena.challengers())
        semaphore = asyncio.Semaphore(self.max_parallel)
        counts = {policy.id: 0 for policy in policies}

        async def one(policy: ComputePolicy, case: BenchmarkCase) -> None:
            async with semaphore:
                outcome = await executor(policy, case)
            self.arena.record_trial(
                policy_id=policy.id,
                task_key=case.id,
                quality=outcome.quality,
                cost_usd=outcome.cost_usd,
                evidence_strength=outcome.evidence_strength,
                passed=outcome.passed,
                source="benchmark",
                latency_ms=outcome.latency_ms,
            )
            counts[policy.id] += 1

        await asyncio.gather(*(one(policy, case) for case in cases for policy in policies))
        return counts
