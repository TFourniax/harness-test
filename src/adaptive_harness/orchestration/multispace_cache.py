from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from adaptive_harness.orchestration.vector_cache import (
    CacheLookup,
    HashingVectorizer,
    SemanticWorkCache,
    _iso,
    _utcnow,
)


_PROCEDURE_TERMS = {
    "audit", "analyze", "analyse", "benchmark", "build", "classify", "compare",
    "debug", "design", "extract", "fix", "implement", "inspect", "investigate",
    "measure", "migrate", "optimize", "plan", "prove", "refactor", "research",
    "review", "scrape", "search", "summarize", "test", "trace", "validate",
    "verify", "write", "architecture", "routing", "cache", "vector", "sql",
    "python", "javascript", "typescript", "api", "http", "git", "github",
}
_PROCEDURE_CANONICAL = {
    "audit": "inspect",
    "review": "inspect",
    "inspect": "inspect",
    "analyze": "analyze",
    "analyse": "analyze",
    "investigate": "research",
    "research": "research",
    "search": "research",
    "scrape": "retrieve",
    "extract": "retrieve",
    "debug": "repair",
    "fix": "repair",
    "refactor": "repair",
    "build": "implement",
    "implement": "implement",
    "write": "implement",
    "test": "validate",
    "validate": "validate",
    "verify": "validate",
    "benchmark": "measure",
    "measure": "measure",
    "plan": "design",
    "design": "design",
    "architecture": "design",
}
_ENTITY_RE = re.compile(
    r"https?://[^\s]+|`[^`]+`|(?:[\w.-]+/){1,}[\w.-]+|"
    r"\b(?:[A-Fa-f0-9]{8,}|\d+(?:\.\d+){1,}|\d{3,})\b"
)


class MultiSpaceVectorizer:
    """Local hybrid representation with separate intent/procedure/entity/full spaces."""

    def __init__(self, dimensions: int = 256) -> None:
        self.base = HashingVectorizer(dimensions)

    @staticmethod
    def entities(text: str) -> list[str]:
        values = []
        for match in _ENTITY_RE.findall(text):
            token = HashingVectorizer.normalize(match.strip("`'\".,;:()[]{}"))
            if token and token not in values:
                values.append(token)
        return sorted(values)

    def split(self, text: str) -> dict[str, str]:
        normalized = HashingVectorizer.normalize(text)
        tokens = normalized.split()
        entities = set(self.entities(text))
        intent_tokens = [tok for tok in tokens if tok not in entities][:160]
        procedure_tokens = []
        for tok in tokens:
            if tok in _PROCEDURE_TERMS:
                procedure_tokens.append(_PROCEDURE_CANONICAL.get(tok, tok))
            elif tok.endswith((".py", ".js", ".ts", ".tsx", ".sql", ".md", ".json", ".yaml", ".yml")):
                procedure_tokens.append(tok)
        return {
            "intent": " ".join(intent_tokens),
            "procedure": " ".join(procedure_tokens),
            "entities": " ".join(sorted(entities)),
            "full": normalized,
        }

    def encode(self, text: str) -> dict[str, list[float]]:
        spaces = self.split(text)
        return {name: self.base.encode(value) for name, value in spaces.items()}

    @staticmethod
    def entity_overlap(a: list[str], b: list[str]) -> float:
        sa, sb = set(a), set(b)
        if not sa and not sb:
            return 1.0
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def scores(
        self,
        query_text: str,
        candidate_text: str,
        candidate_vectors: dict[str, list[float]] | None = None,
    ) -> dict[str, float]:
        q = self.encode(query_text)
        c = candidate_vectors or self.encode(candidate_text)
        scores = {
            name: self.base.cosine(q.get(name, []), c.get(name, []))
            for name in ("intent", "procedure", "entities", "full")
        }
        q_entities = self.entities(query_text)
        c_entities = self.entities(candidate_text)
        scores["entity_overlap"] = self.entity_overlap(q_entities, c_entities)

        weights = {"intent": 0.34, "procedure": 0.22, "entities": 0.10, "full": 0.34}
        q_split = self.split(query_text)
        c_split = self.split(candidate_text)
        active = [name for name in weights if q_split.get(name) and c_split.get(name)]
        if not active:
            scores["hybrid"] = scores["full"]
        else:
            denom = sum(weights[name] for name in active)
            scores["hybrid"] = sum(weights[name] * scores[name] for name in active) / denom
        return scores


class MultiSpaceSemanticWorkCache(SemanticWorkCache):
    """Hybrid semantic cache with entity guards and online threshold calibration."""

    def __init__(
        self,
        path: str | Path,
        dimensions: int = 256,
        *,
        calibration_min_samples: int = 8,
        precision_target: float = 0.995,
        entity_overlap_direct: float = 0.90,
        procedure_floor_direct: float = 0.90,
    ) -> None:
        super().__init__(path, dimensions=max(32, dimensions))
        self.multi = MultiSpaceVectorizer(dimensions)
        self.calibration_min_samples = calibration_min_samples
        self.precision_target = precision_target
        self.entity_overlap_direct = entity_overlap_direct
        self.procedure_floor_direct = procedure_floor_direct
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS work_cache_v2 (
              key TEXT PRIMARY KEY,
              namespace TEXT NOT NULL,
              kind TEXT NOT NULL,
              semantic_text TEXT NOT NULL,
              vectors_json TEXT NOT NULL,
              value_json TEXT NOT NULL,
              context_fingerprint TEXT NOT NULL,
              verified INTEGER NOT NULL,
              allow_direct INTEGER NOT NULL,
              expires_at TEXT,
              created_at TEXT NOT NULL,
              last_access TEXT NOT NULL,
              hits INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        self.db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_work_cache_v2_scope
            ON work_cache_v2(namespace, kind, context_fingerprint)
            """
        )
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_calibration (
              namespace TEXT NOT NULL,
              kind TEXT NOT NULL,
              samples INTEGER NOT NULL DEFAULT 0,
              accepted INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(namespace, kind)
            )
            """
        )
        self.db.commit()

    def put(
        self,
        *,
        namespace: str,
        kind: str,
        semantic_text: str,
        context_fingerprint: str,
        value: dict[str, Any],
        verified: bool,
        allow_direct: bool,
        ttl_seconds: int | None,
    ) -> str:
        from datetime import timedelta

        key = self.exact_key(namespace, kind, semantic_text, context_fingerprint)
        now = _utcnow()
        expires = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        vectors = self.multi.encode(semantic_text)
        self.db.execute(
            """
            INSERT INTO work_cache_v2(
              key, namespace, kind, semantic_text, vectors_json, value_json,
              context_fingerprint, verified, allow_direct, expires_at,
              created_at, last_access, hits
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(key) DO UPDATE SET
              semantic_text=excluded.semantic_text,
              vectors_json=excluded.vectors_json,
              value_json=excluded.value_json,
              verified=excluded.verified,
              allow_direct=excluded.allow_direct,
              expires_at=excluded.expires_at,
              last_access=excluded.last_access
            """,
            (
                key,
                namespace,
                kind,
                semantic_text,
                json.dumps(vectors),
                json.dumps(value, ensure_ascii=False),
                context_fingerprint,
                int(verified),
                int(allow_direct),
                _iso(expires) if expires else None,
                _iso(now),
                _iso(now),
            ),
        )
        self.db.commit()
        return key

    def _effective_direct_threshold(self, namespace: str, kind: str, base_threshold: float) -> float:
        row = self.db.execute(
            """
            SELECT samples, accepted FROM cache_calibration
            WHERE namespace=? AND kind=?
            """,
            (namespace, kind),
        ).fetchone()
        if not row or int(row[0]) < self.calibration_min_samples:
            return base_threshold
        samples, accepted = int(row[0]), int(row[1])
        precision = accepted / samples if samples else 1.0
        if precision >= self.precision_target:
            return base_threshold
        penalty = min(0.014, (self.precision_target - precision) * 0.08)
        return min(0.9995, base_threshold + penalty)

    def record_feedback(self, *, namespace: str, kind: str, accepted: bool) -> None:
        self.db.execute(
            """
            INSERT INTO cache_calibration(namespace, kind, samples, accepted)
            VALUES(?,?,1,?)
            ON CONFLICT(namespace, kind) DO UPDATE SET
              samples=samples+1,
              accepted=accepted+excluded.accepted
            """,
            (namespace, kind, int(accepted)),
        )
        self.db.commit()

    def lookup(
        self,
        *,
        namespace: str,
        kind: str,
        semantic_text: str,
        context_fingerprint: str,
        direct_threshold: float = 0.985,
        reference_threshold: float = 0.90,
        allow_semantic_direct: bool = True,
    ) -> CacheLookup:
        now = _iso(_utcnow())
        key = self.exact_key(namespace, kind, semantic_text, context_fingerprint)
        row = self.db.execute(
            """
            SELECT key, value_json, verified, allow_direct
            FROM work_cache_v2
            WHERE key=? AND (expires_at IS NULL OR expires_at>?)
            """,
            (key, now),
        ).fetchone()
        if row:
            self._touch_v2(row[0])
            if bool(row[2]):
                return CacheLookup("exact", json.loads(row[1]), 1.0, row[0])
            return CacheLookup("semantic_reference", json.loads(row[1]), 1.0, row[0])

        rows = self.db.execute(
            """
            SELECT key, semantic_text, vectors_json, value_json, verified, allow_direct
            FROM work_cache_v2
            WHERE namespace=? AND kind=? AND context_fingerprint=?
              AND (expires_at IS NULL OR expires_at>?)
            ORDER BY last_access DESC LIMIT 256
            """,
            (namespace, kind, context_fingerprint, now),
        ).fetchall()
        best = None
        best_scores: dict[str, float] | None = None
        for candidate in rows:
            scores = self.multi.scores(semantic_text, candidate[1], json.loads(candidate[2]))
            if best_scores is None or scores["hybrid"] > best_scores["hybrid"]:
                best = candidate
                best_scores = scores

        if best is None or best_scores is None or best_scores["hybrid"] < reference_threshold:
            return CacheLookup("miss")

        self._touch_v2(best[0])
        value = json.loads(best[3])
        can_direct = bool(best[4]) and bool(best[5]) and allow_semantic_direct
        effective_direct = self._effective_direct_threshold(namespace, kind, direct_threshold)
        procedure_query = self.multi.split(semantic_text)["procedure"]
        procedure_candidate = self.multi.split(best[1])["procedure"]
        procedure_safe = (
            not procedure_query
            or not procedure_candidate
            or best_scores["procedure"] >= self.procedure_floor_direct
        )
        entity_safe = best_scores["entity_overlap"] >= self.entity_overlap_direct

        if (
            can_direct
            and best_scores["hybrid"] >= effective_direct
            and procedure_safe
            and entity_safe
        ):
            return CacheLookup("exact", value, best_scores["hybrid"], best[0])
        return CacheLookup("semantic_reference", value, best_scores["hybrid"], best[0])

    def _touch_v2(self, key: str) -> None:
        self.db.execute(
            "UPDATE work_cache_v2 SET hits=hits+1, last_access=? WHERE key=?",
            (_iso(_utcnow()), key),
        )
        self.db.commit()
