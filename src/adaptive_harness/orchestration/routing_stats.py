from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RouteStats:
    samples: int
    successes: int
    avg_cost_usd: float

    @property
    def success_rate(self) -> float:
        # Conservative beta-binomial posterior mean avoids overreacting to tiny samples.
        return (self.successes + 2.0) / (self.samples + 4.0)


class RoutingStatsStore:
    """Local empirical model-routing memory keyed by model/profile/difficulty bucket."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS routing_stats (
              model TEXT NOT NULL,
              role TEXT NOT NULL,
              profile TEXT NOT NULL,
              difficulty_bucket INTEGER NOT NULL,
              samples INTEGER NOT NULL DEFAULT 0,
              successes INTEGER NOT NULL DEFAULT 0,
              total_cost_usd REAL NOT NULL DEFAULT 0,
              PRIMARY KEY(model, role, profile, difficulty_bucket)
            )
            """
        )
        self.db.commit()

    @staticmethod
    def bucket(difficulty: float) -> int:
        return min(4, max(0, int(difficulty * 5)))

    def record(
        self,
        *,
        model: str,
        role: str,
        profile: str,
        difficulty: float,
        succeeded: bool,
        cost_usd: float,
    ) -> None:
        bucket = self.bucket(difficulty)
        self.db.execute(
            """
            INSERT INTO routing_stats(model, role, profile, difficulty_bucket, samples, successes, total_cost_usd)
            VALUES(?,?,?,?,1,?,?)
            ON CONFLICT(model, role, profile, difficulty_bucket) DO UPDATE SET
              samples=samples+1,
              successes=successes+excluded.successes,
              total_cost_usd=total_cost_usd+excluded.total_cost_usd
            """,
            (model, role, profile, bucket, int(succeeded), max(0.0, cost_usd)),
        )
        self.db.commit()

    def get(self, *, model: str, role: str, profile: str, difficulty: float) -> RouteStats | None:
        row = self.db.execute(
            """
            SELECT samples, successes, total_cost_usd FROM routing_stats
            WHERE model=? AND role=? AND profile=? AND difficulty_bucket=?
            """,
            (model, role, profile, self.bucket(difficulty)),
        ).fetchone()
        if not row:
            return None
        samples, successes, total = int(row[0]), int(row[1]), float(row[2])
        return RouteStats(samples, successes, total / samples if samples else 0.0)
