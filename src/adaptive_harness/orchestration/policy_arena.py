from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class ComputePolicy:
    id: str
    name: str
    params: dict[str, float]
    status: Literal["champion", "challenger", "retired"]


@dataclass(frozen=True)
class PolicyComparison:
    champion_id: str
    challenger_id: str
    matched_trials: int
    champion_quality: float
    challenger_quality: float
    champion_cost: float
    challenger_cost: float
    champion_pass_rate: float
    challenger_pass_rate: float
    promotable: bool
    reason: str

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "champion_id": self.champion_id,
                "challenger_id": self.challenger_id,
                "matched_trials": self.matched_trials,
                "champion_quality": round(self.champion_quality, 12),
                "challenger_quality": round(self.challenger_quality, 12),
                "champion_cost": round(self.champion_cost, 12),
                "challenger_cost": round(self.challenger_cost, 12),
                "champion_pass_rate": round(self.champion_pass_rate, 12),
                "challenger_pass_rate": round(self.challenger_pass_rate, 12),
                "promotable": self.promotable,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class PolicyArenaStore:
    """Evidence-backed champion/challenger registry for compute policies.

    Counterfactual/shadow observations may be stored for diagnosis, but only real ``benchmark``
    trials are eligible for promotion recommendations. The arena never modifies capability, agent,
    round, or dollar ceilings.

    The constructor accepts either explicit thresholds (the v0.5 API) or a compute-economy config
    object (the v0.6 Policy Lab API). Explicit values remain the public defaults; when ``config`` is
    supplied its policy fields become the source of truth. This keeps old callers compatible while
    avoiding threshold drift between the arena and the lab.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        config: Any | None = None,
        min_matched_trials: int = 12,
        quality_regression_tolerance: float = 0.01,
        quality_gain_target: float = 0.02,
        cost_reduction_target: float = 0.10,
        cost_tolerance: float = 0.05,
        evidence_floor: float = 0.60,
    ) -> None:
        if config is not None:
            min_matched_trials = int(
                getattr(config, "policy_min_matched_trials", min_matched_trials)
            )
            quality_regression_tolerance = float(
                getattr(
                    config,
                    "policy_quality_regression_tolerance",
                    quality_regression_tolerance,
                )
            )
            quality_gain_target = float(
                getattr(config, "policy_quality_gain_target", quality_gain_target)
            )
            cost_reduction_target = float(
                getattr(config, "policy_cost_reduction_target", cost_reduction_target)
            )
            cost_tolerance = float(
                getattr(config, "policy_cost_tolerance", cost_tolerance)
            )
            evidence_floor = float(
                getattr(config, "policy_evidence_floor", evidence_floor)
            )

        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.min_matched_trials = max(1, int(min_matched_trials))
        self.quality_regression_tolerance = max(0.0, float(quality_regression_tolerance))
        self.quality_gain_target = max(0.0, float(quality_gain_target))
        self.cost_reduction_target = max(0.0, float(cost_reduction_target))
        self.cost_tolerance = max(0.0, float(cost_tolerance))
        self.evidence_floor = max(0.0, min(1.0, float(evidence_floor)))
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS compute_policies (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              params_json TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_trials (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              policy_id TEXT NOT NULL,
              task_key TEXT NOT NULL,
              source TEXT NOT NULL,
              quality REAL NOT NULL,
              cost_usd REAL NOT NULL,
              evidence_strength REAL NOT NULL,
              passed INTEGER NOT NULL,
              latency_ms REAL NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_policy_trials_match "
            "ON policy_trials(policy_id, source, task_key)"
        )
        self.db.commit()
        self.bootstrap_defaults()

    def bootstrap_defaults(self) -> None:
        defaults = [
            (
                "balanced-v1",
                "Balanced evidence/cost policy",
                {},
                "champion",
            ),
            (
                "frugal-v1",
                "Frugal challenger",
                {
                    "cost_weight_multiplier": 1.35,
                    "min_expected_gain_multiplier": 1.20,
                    "success_target_delta": -0.01,
                },
                "challenger",
            ),
            (
                "quality-v1",
                "Quality challenger",
                {
                    "cost_weight_multiplier": 0.76,
                    "min_expected_gain_multiplier": 0.82,
                    "success_target_delta": 0.035,
                },
                "challenger",
            ),
        ]
        for policy_id, name, params, status in defaults:
            self.db.execute(
                """
                INSERT OR IGNORE INTO compute_policies(id, name, params_json, status, created_at)
                VALUES(?,?,?,?,?)
                """,
                (policy_id, name, json.dumps(params, sort_keys=True), status, _utcnow()),
            )
        champion_count = self.db.execute(
            "SELECT COUNT(*) FROM compute_policies WHERE status='champion'"
        ).fetchone()[0]
        if champion_count == 0:
            self.db.execute(
                "UPDATE compute_policies SET status='champion' WHERE id='balanced-v1'"
            )
        self.db.commit()

    def policies(self, status: str | None = None) -> list[ComputePolicy]:
        query = "SELECT id, name, params_json, status FROM compute_policies"
        args: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status=?"
            args = (status,)
        rows = self.db.execute(query + " ORDER BY id", args).fetchall()
        return [
            ComputePolicy(str(row[0]), str(row[1]), json.loads(row[2]), str(row[3]))
            for row in rows
        ]

    def champion(self) -> ComputePolicy:
        row = self.db.execute(
            "SELECT id, name, params_json, status FROM compute_policies "
            "WHERE status='champion' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("compute policy arena has no champion")
        return ComputePolicy(str(row[0]), str(row[1]), json.loads(row[2]), str(row[3]))

    def challengers(self) -> list[ComputePolicy]:
        return self.policies("challenger")

    def record_trial(
        self,
        *,
        policy_id: str,
        task_key: str,
        quality: float,
        cost_usd: float,
        evidence_strength: float,
        passed: bool,
        source: Literal["benchmark", "live", "shadow", "matched_action"] = "benchmark",
        latency_ms: float = 0.0,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO policy_trials(
              policy_id, task_key, source, quality, cost_usd,
              evidence_strength, passed, latency_ms, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                policy_id,
                task_key,
                source,
                max(0.0, min(1.0, float(quality))),
                max(0.0, float(cost_usd)),
                max(0.0, min(1.0, float(evidence_strength))),
                int(passed),
                max(0.0, float(latency_ms)),
                _utcnow(),
            ),
        )
        self.db.commit()

    def _latest_eligible(self, policy_id: str) -> dict[str, tuple[float, float, float, int]]:
        rows = self.db.execute(
            """
            SELECT task_key, quality, cost_usd, evidence_strength, passed, id
            FROM policy_trials
            WHERE policy_id=? AND source='benchmark' AND evidence_strength>=?
            ORDER BY id DESC
            """,
            (policy_id, self.evidence_floor),
        ).fetchall()
        latest: dict[str, tuple[float, float, float, int]] = {}
        for task_key, quality, cost, evidence, passed, _id in rows:
            if task_key not in latest:
                latest[str(task_key)] = (
                    float(quality),
                    float(cost),
                    float(evidence),
                    int(passed),
                )
        return latest

    def compare(self, challenger_id: str) -> PolicyComparison:
        champion = self.champion()
        if challenger_id == champion.id:
            raise ValueError("challenger must differ from champion")
        champion_rows = self._latest_eligible(champion.id)
        challenger_rows = self._latest_eligible(challenger_id)
        keys = sorted(set(champion_rows) & set(challenger_rows))
        if not keys:
            return PolicyComparison(
                champion.id,
                challenger_id,
                0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                False,
                "no matched benchmark trials with sufficient evidence",
            )

        def weighted(policy_rows: dict[str, tuple[float, float, float, int]]):
            denom = sum(policy_rows[key][2] for key in keys)
            denom = max(denom, 1e-9)
            quality = sum(policy_rows[key][0] * policy_rows[key][2] for key in keys) / denom
            cost = sum(policy_rows[key][1] * policy_rows[key][2] for key in keys) / denom
            pass_rate = sum(policy_rows[key][3] * policy_rows[key][2] for key in keys) / denom
            return quality, cost, pass_rate

        c_quality, c_cost, c_pass = weighted(champion_rows)
        h_quality, h_cost, h_pass = weighted(challenger_rows)
        matched = len(keys)
        if matched < self.min_matched_trials:
            return PolicyComparison(
                champion.id,
                challenger_id,
                matched,
                c_quality,
                h_quality,
                c_cost,
                h_cost,
                c_pass,
                h_pass,
                False,
                f"need at least {self.min_matched_trials} matched benchmark trials",
            )

        quality_delta = h_quality - c_quality
        cost_ratio = h_cost / max(c_cost, 1e-9)
        pass_delta = h_pass - c_pass
        quality_win = (
            quality_delta >= self.quality_gain_target
            and cost_ratio <= 1.0 + self.cost_tolerance
        )
        cost_win = (
            quality_delta >= -self.quality_regression_tolerance
            and cost_ratio <= 1.0 - self.cost_reduction_target
        )
        pass_safe = pass_delta >= -self.quality_regression_tolerance
        promotable = bool((quality_win or cost_win) and pass_safe)
        reason = (
            f"matched={matched} quality_delta={quality_delta:+.4f} "
            f"cost_ratio={cost_ratio:.3f} pass_delta={pass_delta:+.4f}; "
            + ("challenger clears evidence gates" if promotable else "challenger does not clear gates")
        )
        return PolicyComparison(
            champion.id,
            challenger_id,
            matched,
            c_quality,
            h_quality,
            c_cost,
            h_cost,
            c_pass,
            h_pass,
            promotable,
            reason,
        )

    def recommendations(self) -> list[PolicyComparison]:
        return [self.compare(policy.id) for policy in self.challengers()]

    def promote(self, challenger_id: str, *, approved_fingerprint: str) -> None:
        """Promote only when governance approved this exact benchmark comparison."""
        comparison = self.compare(challenger_id)
        if not comparison.promotable:
            raise ValueError(f"challenger is not promotable: {comparison.reason}")
        if not approved_fingerprint or approved_fingerprint != comparison.fingerprint:
            raise ValueError(
                "policy promotion fingerprint does not match the current benchmark evidence"
            )
        champion = self.champion()
        self.db.execute(
            "UPDATE compute_policies SET status='retired' WHERE id=?", (champion.id,)
        )
        self.db.execute(
            "UPDATE compute_policies SET status='champion' WHERE id=?", (challenger_id,)
        )
        self.db.commit()
