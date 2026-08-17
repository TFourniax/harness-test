from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

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
    samples: float
    successes: float
    avg_cost_usd: float
    avg_quality_gain: float
    evidence_mass: float = 0.0

    @property
    def success_rate(self) -> float:
        denominator = self.evidence_mass if self.evidence_mass > 0 else self.samples
        numerator = self.successes
        # Conservative Beta(2,2) posterior mean.
        return (numerator + 2.0) / (denominator + 4.0)


@dataclass(frozen=True)
class ComputeBid:
    action: ComputeAction
    expected_success: float
    expected_gain: float
    estimated_cost_usd: float
    utility: float
    reason: str
    policy_id: str = "balanced-v1"


class ComputeEconomyStore:
    """Persistent local outcome ledger for compute strategies.

    v0.5 keeps the v0.4 aggregate for compatibility and adds a decayed evidence-weighted table.
    Weak/model-only outcomes therefore have much less influence than deterministic or otherwise
    strongly evidenced outcomes. Geometric time decay lets the router adapt when provider quality
    or pricing changes rather than treating old history as permanent truth.
    """

    def __init__(self, path: str | Path, *, evidence_half_life_days: float = 45.0) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.evidence_half_life_days = max(1.0, float(evidence_half_life_days))
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
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_evidence_stats (
              action TEXT NOT NULL,
              profile TEXT NOT NULL,
              difficulty_bucket INTEGER NOT NULL,
              critical INTEGER NOT NULL,
              effective_samples REAL NOT NULL DEFAULT 0,
              evidence_mass REAL NOT NULL DEFAULT 0,
              success_mass REAL NOT NULL DEFAULT 0,
              total_cost_usd REAL NOT NULL DEFAULT 0,
              total_verified_gain REAL NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(action, profile, difficulty_bucket, critical)
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS compute_decisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              policy_id TEXT NOT NULL,
              action TEXT NOT NULL,
              profile TEXT NOT NULL,
              difficulty_bucket INTEGER NOT NULL,
              critical INTEGER NOT NULL,
              current_confidence REAL NOT NULL,
              remaining_budget_usd REAL,
              expected_success REAL NOT NULL,
              expected_gain REAL NOT NULL,
              estimated_cost_usd REAL NOT NULL,
              utility REAL NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        self.db.commit()

    @staticmethod
    def bucket(difficulty: float) -> int:
        return min(5, max(0, int(difficulty * 6)))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _decay(self, updated_at: str) -> float:
        try:
            then = datetime.fromisoformat(updated_at)
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            elapsed_seconds = max(0.0, (self._now() - then).total_seconds())
        except Exception:
            return 1.0
        # Sub-minute decay is economically meaningless at a day-scale half-life and only
        # introduces floating-point drift into immediately-read evidence aggregates.
        if elapsed_seconds < 60.0:
            return 1.0
        elapsed_days = elapsed_seconds / 86400.0
        return 0.5 ** (elapsed_days / self.evidence_half_life_days)

    def _key(self, action: ComputeAction, task: WorkItem) -> tuple[Any, ...]:
        return (
            action.value,
            task.profile or "generic",
            self.bucket(task.difficulty),
            int(task.critical),
        )

    def get(self, action: ComputeAction, task: WorkItem) -> StrategyStats | None:
        key = self._key(action, task)
        row = self.db.execute(
            """
            SELECT effective_samples, evidence_mass, success_mass, total_cost_usd,
                   total_verified_gain, updated_at
            FROM strategy_evidence_stats
            WHERE action=? AND profile=? AND difficulty_bucket=? AND critical=?
            """,
            key,
        ).fetchone()
        if row:
            decay = self._decay(str(row[5]))
            samples = float(row[0]) * decay
            evidence_mass = float(row[1]) * decay
            success_mass = float(row[2]) * decay
            total_cost = float(row[3]) * decay
            verified_gain = float(row[4]) * decay
            return StrategyStats(
                samples=samples,
                successes=success_mass,
                avg_cost_usd=total_cost / samples if samples > 1e-9 else 0.0,
                avg_quality_gain=(
                    verified_gain / evidence_mass if evidence_mass > 1e-9 else 0.0
                ),
                evidence_mass=evidence_mass,
            )

        # Backward-compatible v0.4 history remains useful until v0.5 evidence accumulates.
        row = self.db.execute(
            """
            SELECT samples, successes, total_cost_usd, total_quality_gain
            FROM strategy_stats
            WHERE action=? AND profile=? AND difficulty_bucket=? AND critical=?
            """,
            key,
        ).fetchone()
        if not row:
            return None
        samples = int(row[0])
        return StrategyStats(
            samples=float(samples),
            successes=float(row[1]),
            avg_cost_usd=float(row[2]) / samples if samples else 0.0,
            avg_quality_gain=float(row[3]) / samples if samples else 0.0,
            evidence_mass=float(samples),
        )

    def record(
        self,
        action: ComputeAction,
        task: WorkItem,
        *,
        succeeded: bool,
        cost_usd: float,
        quality_gain: float,
        evidence_strength: float = 1.0,
        outcome_score: float | None = None,
    ) -> None:
        cost = max(0.0, float(cost_usd))
        gain = max(0.0, min(1.0, float(quality_gain)))
        evidence = max(0.0, min(1.0, float(evidence_strength)))
        if outcome_score is not None:
            outcome = max(0.0, min(1.0, float(outcome_score)))
        else:
            outcome = 1.0 if succeeded else 0.0

        key = self._key(action, task)
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
            (*key, int(succeeded), cost, gain),
        )

        existing = self.db.execute(
            """
            SELECT effective_samples, evidence_mass, success_mass, total_cost_usd,
                   total_verified_gain, updated_at
            FROM strategy_evidence_stats
            WHERE action=? AND profile=? AND difficulty_bucket=? AND critical=?
            """,
            key,
        ).fetchone()
        now = self._now().isoformat()
        if existing:
            decay = self._decay(str(existing[5]))
            samples = float(existing[0]) * decay + 1.0
            evidence_mass = float(existing[1]) * decay + evidence
            success_mass = float(existing[2]) * decay + outcome * evidence
            total_cost = float(existing[3]) * decay + cost
            verified_gain = float(existing[4]) * decay + gain * evidence
            self.db.execute(
                """
                UPDATE strategy_evidence_stats SET
                  effective_samples=?, evidence_mass=?, success_mass=?, total_cost_usd=?,
                  total_verified_gain=?, updated_at=?
                WHERE action=? AND profile=? AND difficulty_bucket=? AND critical=?
                """,
                (
                    samples,
                    evidence_mass,
                    success_mass,
                    total_cost,
                    verified_gain,
                    now,
                    *key,
                ),
            )
        else:
            self.db.execute(
                """
                INSERT INTO strategy_evidence_stats(
                  action, profile, difficulty_bucket, critical, effective_samples,
                  evidence_mass, success_mass, total_cost_usd, total_verified_gain, updated_at
                ) VALUES(?,?,?,?,1,?,?,?,?,?)
                """,
                (*key, evidence, outcome * evidence, cost, gain * evidence, now),
            )
        self.db.commit()

    def record_decision(
        self,
        *,
        policy_id: str,
        bid: ComputeBid,
        task: WorkItem,
        current_confidence: float,
        remaining_budget_usd: float | None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO compute_decisions(
              policy_id, action, profile, difficulty_bucket, critical, current_confidence,
              remaining_budget_usd, expected_success, expected_gain, estimated_cost_usd,
              utility, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                policy_id,
                bid.action.value,
                task.profile or "generic",
                self.bucket(task.difficulty),
                int(task.critical),
                max(0.0, min(1.0, current_confidence)),
                remaining_budget_usd,
                bid.expected_success,
                bid.expected_gain,
                bid.estimated_cost_usd,
                bid.utility,
                self._now().isoformat(),
            ),
        )
        self.db.commit()


class ComputeMarket:
    """Choose the smallest useful compute coalition under uncertainty and budget."""

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

    @staticmethod
    def _p(policy: dict[str, float] | None, key: str, default: float) -> float:
        if not policy:
            return default
        return float(policy.get(key, default))

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
        policy: dict[str, float] | None = None,
        policy_id: str = "balanced-v1",
    ) -> ComputeBid:
        stats = self._stats(action, task)
        prior = self._PRIORS[action]
        evidence_samples = stats.evidence_mass if stats else 0.0
        enough_evidence = (
            stats is not None
            and evidence_samples >= self.economy.min_strategy_evidence_mass
        )
        expected_success = stats.success_rate if enough_evidence else prior
        empirical_gain = (
            max(0.0, stats.avg_quality_gain) if enough_evidence else expected_success
        )

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
        cost_weight = self.economy.cost_weight * self._p(
            policy, "cost_weight_multiplier", 1.0
        )
        efficiency = expected_gain / (1.0 + cost_weight * relative_cost)

        target = (
            self.economy.critical_success_target
            if task.critical
            else self.economy.normal_success_target
        )
        target += self._p(policy, "success_target_delta", 0.0)
        target = max(0.0, min(0.995, target))
        reliability_penalty = max(0.0, target - expected_success)
        if task.critical:
            reliability_penalty *= self.economy.critical_reliability_penalty

        exploration = 0.0
        if self.economy.exploration_rate > 0:
            exploration = self.economy.exploration_rate / math.sqrt(evidence_samples + 1.0)

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
            f"est_cost=${estimated_cost:.6f} evidence={evidence_samples:.2f} "
            f"policy={policy_id} utility={utility:.3f}"
        )
        return ComputeBid(
            action=action,
            expected_success=expected_success,
            expected_gain=expected_gain,
            estimated_cost_usd=estimated_cost,
            utility=utility,
            reason=reason,
            policy_id=policy_id,
        )

    def choose(
        self,
        task: WorkItem,
        *,
        current_confidence: float = 0.0,
        remaining_budget_usd: float | None = None,
        policy: dict[str, float] | None = None,
        policy_id: str = "balanced-v1",
        record_decision: bool = False,
    ) -> ComputeBid:
        actions = self._eligible(task)
        bids = [
            self.quote(
                action,
                task,
                current_confidence=current_confidence,
                remaining_budget_usd=remaining_budget_usd,
                policy=policy,
                policy_id=policy_id,
            )
            for action in actions
        ]
        # Budget is a hard constraint, not merely a utility penalty. If no eligible
        # coalition can fit, stop rather than letting criticality or exploration overspend.
        if remaining_budget_usd is not None:
            affordable = [
                bid for bid in bids
                if bid.estimated_cost_usd <= max(0.0, remaining_budget_usd)
            ]
            if not affordable:
                best = ComputeBid(
                    action=ComputeAction.STOP,
                    expected_success=current_confidence,
                    expected_gain=0.0,
                    estimated_cost_usd=0.0,
                    utility=0.0,
                    reason="hard remaining budget cannot fund any eligible compute action",
                    policy_id=policy_id,
                )
                if record_decision and self.store is not None:
                    self.store.record_decision(
                        policy_id=policy_id,
                        bid=best,
                        task=task,
                        current_confidence=current_confidence,
                        remaining_budget_usd=remaining_budget_usd,
                    )
                return best
            bids = affordable

        best = max(bids, key=lambda bid: bid.utility)

        has_learned_evidence = any(
            (self._stats(action, task) is not None)
            and self._stats(action, task).evidence_mass
            >= self.economy.min_strategy_evidence_mass
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

        min_gain = self.economy.min_expected_gain * self._p(
            policy, "min_expected_gain_multiplier", 1.0
        )
        if (
            not task.critical
            and best.expected_gain < min_gain
            and current_confidence >= self.economy.stop_confidence_floor
        ):
            best = ComputeBid(
                action=ComputeAction.STOP,
                expected_success=current_confidence,
                expected_gain=0.0,
                estimated_cost_usd=0.0,
                utility=0.0,
                reason="marginal evidence-weighted quality gain below configured floor",
                policy_id=policy_id,
            )
        if record_decision and self.store is not None:
            self.store.record_decision(
                policy_id=policy_id,
                bid=best,
                task=task,
                current_confidence=current_confidence,
                remaining_budget_usd=remaining_budget_usd,
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
        evidence_strength: float = 1.0,
        outcome_score: float | None = None,
    ) -> None:
        if self.store is None or bid.action == ComputeAction.STOP:
            return
        self.store.record(
            bid.action,
            task,
            succeeded=succeeded,
            cost_usd=cost_usd,
            quality_gain=quality_gain,
            evidence_strength=evidence_strength,
            outcome_score=outcome_score,
        )
