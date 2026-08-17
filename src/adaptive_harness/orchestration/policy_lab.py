from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class _Pair:
    task_key: str
    champion_quality: float
    challenger_quality: float
    champion_cost: float
    challenger_cost: float
    champion_success: float
    challenger_success: float


class Interval(BaseModel):
    low: float
    mean: float
    high: float


class PolicyUncertaintyReport(BaseModel):
    champion_id: str
    challenger_id: str
    paired_tasks: int = 0
    quality_delta: Interval
    cost_delta_fraction: Interval
    success_delta: Interval
    robust_quality_noninferior: bool = False
    robust_quality_gain: bool = False
    robust_cost_reduction: bool = False
    recommendation: str = "insufficient"
    fingerprint: str = ""
    task_keys: list[str] = Field(default_factory=list)


class PolicyUncertaintyLab:
    """Paired bootstrap uncertainty over held-out benchmark policy outcomes.

    The unit of resampling is the task key, preserving within-task champion/challenger pairing.
    Live/shadow traces are intentionally excluded from this promotion evidence path.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        evidence_floor: float = 0.60,
        bootstrap_samples: int = 2000,
        seed: int = 0,
    ) -> None:
        self.db_path = str(db_path)
        self.evidence_floor = float(evidence_floor)
        self.bootstrap_samples = max(200, int(bootstrap_samples))
        self.seed = int(seed)

    def _load_policy(self, policy_id: str) -> dict[str, tuple[float, float, float]]:
        if not Path(self.db_path).exists():
            return {}
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute(
                """
                SELECT task_key, AVG(quality), AVG(cost_usd), AVG(passed)
                FROM policy_trials
                WHERE policy_id=? AND source='benchmark' AND evidence_strength>=?
                GROUP BY task_key
                """,
                (policy_id, self.evidence_floor),
            ).fetchall()
        return {
            str(task_key): (float(quality), float(cost), float(success))
            for task_key, quality, cost, success in rows
        }

    def _pairs(self, champion_id: str, challenger_id: str) -> list[_Pair]:
        champion = self._load_policy(champion_id)
        challenger = self._load_policy(challenger_id)
        keys = sorted(set(champion) & set(challenger))
        return [
            _Pair(
                task_key=key,
                champion_quality=champion[key][0],
                challenger_quality=challenger[key][0],
                champion_cost=champion[key][1],
                challenger_cost=challenger[key][1],
                champion_success=champion[key][2],
                challenger_success=challenger[key][2],
            )
            for key in keys
        ]

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return float(ordered[index])

    @classmethod
    def _interval(cls, values: list[float]) -> Interval:
        return Interval(
            low=cls._quantile(values, 0.025),
            mean=cls._mean(values),
            high=cls._quantile(values, 0.975),
        )

    @staticmethod
    def _cost_delta(pair: _Pair) -> float:
        denominator = max(1e-9, pair.champion_cost)
        return (pair.challenger_cost - pair.champion_cost) / denominator

    def compare(
        self,
        *,
        champion_id: str,
        challenger_id: str,
        min_paired_tasks: int = 12,
        quality_regression_tolerance: float = 0.01,
        quality_gain_target: float = 0.02,
        cost_reduction_target: float = 0.10,
        cost_tolerance: float = 0.05,
    ) -> PolicyUncertaintyReport:
        pairs = self._pairs(champion_id, challenger_id)
        task_keys = [pair.task_key for pair in pairs]
        if not pairs:
            return self._report(
                champion_id,
                challenger_id,
                task_keys,
                [],
                [],
                [],
                False,
                False,
                False,
                "insufficient",
            )

        quality_samples: list[float] = []
        cost_samples: list[float] = []
        success_samples: list[float] = []
        seed_material = f"{self.seed}:{champion_id}:{challenger_id}:{','.join(task_keys)}"
        rng = random.Random(int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16))
        n = len(pairs)
        for _ in range(self.bootstrap_samples):
            sample = [pairs[rng.randrange(n)] for _ in range(n)]
            quality_samples.append(
                self._mean(
                    [pair.challenger_quality - pair.champion_quality for pair in sample]
                )
            )
            cost_samples.append(self._mean([self._cost_delta(pair) for pair in sample]))
            success_samples.append(
                self._mean(
                    [pair.challenger_success - pair.champion_success for pair in sample]
                )
            )

        quality = self._interval(quality_samples)
        cost = self._interval(cost_samples)
        success = self._interval(success_samples)
        enough = len(pairs) >= min_paired_tasks
        noninferior = (
            enough
            and quality.low >= -quality_regression_tolerance
            and success.low >= -0.02
        )
        robust_quality_gain = (
            noninferior
            and quality.low >= quality_gain_target
            and cost.high <= cost_tolerance
        )
        robust_cost_reduction = (
            noninferior and cost.high <= -cost_reduction_target
        )
        recommendation = (
            "promotable"
            if robust_quality_gain or robust_cost_reduction
            else "hold"
            if enough
            else "insufficient"
        )
        return self._report(
            champion_id,
            challenger_id,
            task_keys,
            quality_samples,
            cost_samples,
            success_samples,
            noninferior,
            robust_quality_gain,
            robust_cost_reduction,
            recommendation,
        )

    def _report(
        self,
        champion_id: str,
        challenger_id: str,
        task_keys: list[str],
        quality_values: list[float],
        cost_values: list[float],
        success_values: list[float],
        noninferior: bool,
        quality_gain: bool,
        cost_reduction: bool,
        recommendation: str,
    ) -> PolicyUncertaintyReport:
        payload = {
            "champion_id": champion_id,
            "challenger_id": challenger_id,
            "task_keys": task_keys,
            "quality": self._interval(quality_values).model_dump(),
            "cost": self._interval(cost_values).model_dump(),
            "success": self._interval(success_values).model_dump(),
            "robust_quality_noninferior": noninferior,
            "robust_quality_gain": quality_gain,
            "robust_cost_reduction": cost_reduction,
            "recommendation": recommendation,
        }
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return PolicyUncertaintyReport(
            champion_id=champion_id,
            challenger_id=challenger_id,
            paired_tasks=len(task_keys),
            quality_delta=Interval.model_validate(payload["quality"]),
            cost_delta_fraction=Interval.model_validate(payload["cost"]),
            success_delta=Interval.model_validate(payload["success"]),
            robust_quality_noninferior=noninferior,
            robust_quality_gain=quality_gain,
            robust_cost_reduction=cost_reduction,
            recommendation=recommendation,
            fingerprint=fingerprint,
            task_keys=task_keys,
        )
