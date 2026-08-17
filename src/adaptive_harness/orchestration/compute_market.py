from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from adaptive_harness.config import ComputeEconomyConfig, HarnessConfig
from adaptive_harness.orchestration.contracts import WorkItem


class ComputeAction(str, Enum):
    STOP = "stop"
    CHEAP_SINGLE = "cheap_single"
    CHEAP_PAIR = "cheap_pair"
    PRIMARY_SINGLE = "primary_single"
    MIXED_PAIR = "mixed_pair"


@dataclass(frozen=True)
class StrategyStats:
    samples: int
    successes: int
    avg_cost_usd: float
    avg_quality_gain: float

    @property
    def success_rate(self) -> float:
        # Conservative Beta(2,2) prior.
        return (self.successes + 2.0) / (self.samples + 4.0)


@dataclass(frozen=True)
class ComputeBid:
    action: ComputeAction
    expected_success: float
    expected_gain: float
    estimated_cost_usd: float
    utility: float
    reason: str


class ComputeEconomyStore:
    """Persistent local outcome ledger for compute strategies."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_stats (
              action TEXT NOT NULL,
              profile TEXT NOT NULL,
              difficulty_bucket INTEGER NOT NULL,
              critical INTEGER NOT NULL,
              samples INTEGER NOT NULL DEFAULT 0,
              successes INTEGER NOT NULL DEFAULT 0,
              total_cost_usd REAL NOT NULL DEFAULT 0,
              total_quality_gain REAL NOT NULL DEFAULT 0,
              PRIMARY KEY(action, profile, difficulty_bucket, critical)
            )
            """
        )
        self.db.commit()

    @staticmethod
    def bucket(difficulty: float) -> int:
        return min(5, max(0, int(difficulty * 6)))

    def get(self, action: ComputeAction, task: WorkItem) -> StrategyStats | None:
        row = self.db.execute(
            """
            SELECT samples, successes, total_cost_usd, total_quality_gain
            FROM strategy_stats
            WHERE action=? AND profile=? AND difficulty_bucket=? AND critical=?
            """,
            (
                action.value,
                task.profile or "generic",
                self.bucket(task.difficulty),
                int(task.critical),
            ),
        ).fetchone()
        if not row:
            return None
        samples = int(row[0])
        return StrategyStats(
            samples=samples,
            successes=int(row[1]),
            avg_cost_usd=float(row[2]) / samples if samples else 0.0,
            avg_quality_gain=float(row[3]) / samples if samples else 0.0,
        )

    def record(
        self,
        action: ComputeAction,
        task: WorkItem,
        *,
        succeeded: bool,
        cost_usd: float,
        quality_gain: float,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO strategy_stats(
              action, profile, difficulty_bucket, critical,
              samples, successes, total_cost_usd, total_quality_gain
            ) VALUES(?,?,?,?,1,?,?,?)
            ON CONFLICT(action, profile, difficulty_bucket, critical) DO UPDATE SET
              samples=samples+1,
              successes=successes+excluded.successes,
              total_cost_usd=total_cost_usd+excluded.total_cost_usd,
              total_quality_gain=total_quality_gain+excluded.total_quality_gain
            """,
            (
                action.value,
                task.profile or "generic",
                self.bucket(task.difficulty),
                int(task.critical),
                int(succeeded),
                max(0.0, float(cost_usd)),
                max(0.0, min(1.0, float(quality_gain))),
            ),
        )
        self.db.commit()


class ComputeMarket:
    """Choose the smallest useful compute coalition under uncertainty and budget.

    This is deliberately an online contextual bandit-style controller, not an RL policy.
    It starts from transparent priors, learns per task-family/difficulty bucket, and
    exposes every score for auditability.
    """

    _PRIORS = {
        ComputeAction.CHEAP_SINGLE: 0.70,
        ComputeAction.CHEAP_PAIR: 0.82,
        ComputeAction.PRIMARY_SINGLE: 0.88,
        ComputeAction.MIXED_PAIR: 0.93,
    }

    def __init__(
        self,
        config: HarnessConfig,
        store: ComputeEconomyStore | None = None,
    ) -> None:
        self.config = config
        self.economy: ComputeEconomyConfig = (
            config.team.economy if config.team is not None else ComputeEconomyConfig()
        )
        self.store = store

    def _cold_cost(self, action: ComputeAction) -> float:
        cheap = self.economy.cold_start_cheap_call_usd
        primary = self.economy.cold_start_primary_call_usd
        return {
            ComputeAction.CHEAP_SINGLE: cheap,
            ComputeAction.CHEAP_PAIR: 2.0 * cheap,
            ComputeAction.PRIMARY_SINGLE: primary,
            ComputeAction.MIXED_PAIR: cheap + primary,
            ComputeAction.STOP: 0.0,
        }[action]

    def _eligible(self, task: WorkItem) -> list[ComputeAction]:
        if self.config.cheap is None:
            return [ComputeAction.PRIMARY_SINGLE]
        if task.critical:
            if task.difficulty >= self.economy.critical_mixed_pair_difficulty:
                return [ComputeAction.MIXED_PAIR, ComputeAction.PRIMARY_SINGLE]
            return [ComputeAction.PRIMARY_SINGLE, ComputeAction.MIXED_PAIR]
        if task.difficulty >= self.economy.primary_preferred_difficulty:
            return [
                ComputeAction.PRIMARY_SINGLE,
                ComputeAction.MIXED_PAIR,
                ComputeAction.CHEAP_PAIR,
            ]
        if task.difficulty >= self.economy.cheap_pair_min_difficulty:
            return [
                ComputeAction.CHEAP_PAIR,
                ComputeAction.CHEAP_SINGLE,
                ComputeAction.PRIMARY_SINGLE,
                ComputeAction.MIXED_PAIR,
            ]
        return [
            ComputeAction.CHEAP_SINGLE,
            ComputeAction.CHEAP_PAIR,
            ComputeAction.PRIMARY_SINGLE,
        ]

    def _stats(self, action: ComputeAction, task: WorkItem) -> StrategyStats | None:
        return self.store.get(action, task) if self.store is not None else None

    def quote(
        self,
        action: ComputeAction,
        task: WorkItem,
        *,
        current_confidence: float = 0.0,
        remaining_budget_usd: float | None = None,
    ) -> ComputeBid:
        stats = self._stats(action, task)
        prior = self._PRIORS[action]
        samples = stats.samples if stats else 0
        expected_success = (
            stats.success_rate
            if stats is not None and stats.samples >= self.economy.min_strategy_samples
            else prior
        )
        if stats is not None and stats.samples >= self.economy.min_strategy_samples:
            empirical_gain = max(0.0, stats.avg_quality_gain)
        else:
            empirical_gain = expected_success

        uncertainty = max(0.0, 1.0 - current_confidence)
        diversity = {
            ComputeAction.CHEAP_SINGLE: 1.0,
            ComputeAction.CHEAP_PAIR: self.economy.cheap_pair_diversity_multiplier,
            ComputeAction.PRIMARY_SINGLE: 1.05,
            ComputeAction.MIXED_PAIR: self.economy.mixed_pair_diversity_multiplier,
        }[action]
        expected_gain = min(1.0, uncertainty * empirical_gain * diversity)

        estimated_cost = (
            stats.avg_cost_usd
            if stats is not None and stats.avg_cost_usd > 0
            else self._cold_cost(action)
        )
        primary_ref = max(self.economy.cold_start_primary_call_usd, 1e-6)
        relative_cost = estimated_cost / primary_ref
        efficiency = expected_gain / (1.0 + self.economy.cost_weight * relative_cost)

        target = (
            self.economy.critical_success_target
            if task.critical
            else self.economy.normal_success_target
        )
        reliability_penalty = max(0.0, target - expected_success)
        if task.critical:
            reliability_penalty *= self.economy.critical_reliability_penalty

        exploration = 0.0
        if self.economy.exploration_rate > 0:
            exploration = self.economy.exploration_rate / math.sqrt(samples + 1.0)

        budget_penalty = 0.0
        if remaining_budget_usd is not None:
            if estimated_cost > remaining_budget_usd:
                budget_penalty = 10.0
            elif remaining_budget_usd > 0:
                budget_penalty = (
                    self.economy.budget_pressure_weight
                    * estimated_cost
                    / remaining_budget_usd
                )

        utility = efficiency + exploration - reliability_penalty - budget_penalty
        reason = (
            f"p={expected_success:.3f} gain={expected_gain:.3f} "
            f"est_cost=${estimated_cost:.6f} samples={samples} "
            f"utility={utility:.3f}"
        )
        return ComputeBid(
            action=action,
            expected_success=expected_success,
            expected_gain=expected_gain,
            estimated_cost_usd=estimated_cost,
            utility=utility,
            reason=reason,
        )

    def choose(
        self,
        task: WorkItem,
        *,
        current_confidence: float = 0.0,
        remaining_budget_usd: float | None = None,
    ) -> ComputeBid:
        actions = self._eligible(task)
        bids = [
            self.quote(
                action,
                task,
                current_confidence=current_confidence,
                remaining_budget_usd=remaining_budget_usd,
            )
            for action in actions
        ]
        best = max(bids, key=lambda bid: bid.utility)

        has_learned_evidence = any(
            (self._stats(action, task) is not None)
            and self._stats(action, task).samples >= self.economy.min_strategy_samples
            for action in actions
        )
        if not has_learned_evidence:
            if task.critical and task.difficulty >= self.economy.critical_mixed_pair_difficulty:
                mixed = next((b for b in bids if b.action == ComputeAction.MIXED_PAIR), None)
                if mixed is not None and mixed.utility > -9:
                    best = mixed
            elif not task.critical and task.difficulty >= self.economy.primary_preferred_difficulty:
                primary = next((b for b in bids if b.action == ComputeAction.PRIMARY_SINGLE), None)
                if primary is not None and primary.utility > -9:
                    best = primary
            elif not task.critical and task.difficulty >= self.economy.cheap_pair_min_difficulty:
                pair = next((b for b in bids if b.action == ComputeAction.CHEAP_PAIR), None)
                if pair is not None and pair.utility > -9:
                    best = pair
            elif not task.critical:
                cheap = next((b for b in bids if b.action == ComputeAction.CHEAP_SINGLE), None)
                if cheap is not None and cheap.utility > -9:
                    best = cheap

        if (
            not task.critical
            and best.expected_gain < self.economy.min_expected_gain
            and current_confidence >= self.economy.stop_confidence_floor
        ):
            return ComputeBid(
                action=ComputeAction.STOP,
                expected_success=current_confidence,
                expected_gain=0.0,
                estimated_cost_usd=0.0,
                utility=0.0,
                reason="marginal expected quality gain below configured floor",
            )
        return best

    def record(
        self,
        bid: ComputeBid,
        task: WorkItem,
        *,
        succeeded: bool,
        cost_usd: float,
        quality_gain: float,
    ) -> None:
        if self.store is None or bid.action == ComputeAction.STOP:
            return
        self.store.record(
            bid.action,
            task,
            succeeded=succeeded,
            cost_usd=cost_usd,
            quality_gain=quality_gain,
        )
