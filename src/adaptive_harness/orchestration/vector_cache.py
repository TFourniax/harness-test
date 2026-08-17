from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class HashingVectorizer:
    """Zero-cost local semantic vectorizer.

    It deliberately avoids a remote embedding call in the hot path. Tokens and adjacent token
    bigrams are feature-hashed into a fixed vector. Deployments can later replace this component
    with a learned embedding/Qdrant backend without changing cache semantics.
    """

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 32:
            raise ValueError("dimensions must be >= 32")
        self.dimensions = dimensions

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9À-ÿ_./:-]+", text.lower()))

    def encode(self, text: str) -> list[float]:
        tokens = self.normalize(text).split()
        features = tokens + [f"{a}::{b}" for a, b in zip(tokens, tokens[1:])]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self.dimensions
            sign = 1.0 if (value >> 8) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(x * x for x in vector))
        if norm:
            vector = [x / norm for x in vector]
        return vector

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if len(a) != len(b):
            return 0.0
        return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True)
class CacheLookup:
    status: str
    value: dict[str, Any] | None = None
    similarity: float = 0.0
    key: str | None = None


class SemanticWorkCache:
    """Two-zone exact/semantic cache for verified agent work.

    Direct semantic reuse is intentionally stricter than reference retrieval. Volatile work can
    disable direct reuse while still retrieving a prior result as a hint. Context fingerprints
    prevent results from a different repository/data snapshot being treated as equivalent.
    """

    def __init__(self, path: str | Path, dimensions: int = 384) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.vectorizer = HashingVectorizer(dimensions)
        self.db = sqlite3.connect(self.path)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS work_cache (
              key TEXT PRIMARY KEY,
              namespace TEXT NOT NULL,
              kind TEXT NOT NULL,
              semantic_text TEXT NOT NULL,
              vector_json TEXT NOT NULL,
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
            "CREATE INDEX IF NOT EXISTS idx_work_cache_scope ON work_cache(namespace, kind, context_fingerprint)"
        )
        self.db.commit()

    @staticmethod
    def exact_key(namespace: str, kind: str, semantic_text: str, context_fingerprint: str) -> str:
        payload = json.dumps(
            {
                "namespace": namespace,
                "kind": kind,
                "text": HashingVectorizer.normalize(semantic_text),
                "context": context_fingerprint,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
        key = self.exact_key(namespace, kind, semantic_text, context_fingerprint)
        now = _utcnow()
        expires = now + timedelta(seconds=ttl_seconds) if ttl_seconds else None
        vector = self.vectorizer.encode(semantic_text)
        self.db.execute(
            """
            INSERT INTO work_cache(
              key, namespace, kind, semantic_text, vector_json, value_json,
              context_fingerprint, verified, allow_direct, expires_at, created_at, last_access, hits
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)
            ON CONFLICT(key) DO UPDATE SET
              semantic_text=excluded.semantic_text,
              vector_json=excluded.vector_json,
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
                json.dumps(vector),
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
            SELECT key, value_json, verified, allow_direct FROM work_cache
            WHERE key=? AND (expires_at IS NULL OR expires_at>?)
            """,
            (key, now),
        ).fetchone()
        if row:
            self._touch(row[0])
            # Exact same verified input/context is safe to reuse even when semantic direct reuse
            # is disabled for the broader task family.
            if bool(row[2]):
                return CacheLookup("exact", json.loads(row[1]), 1.0, row[0])
            return CacheLookup("semantic_reference", json.loads(row[1]), 1.0, row[0])

        query = self.vectorizer.encode(semantic_text)
        rows = self.db.execute(
            """
            SELECT key, vector_json, value_json, verified, allow_direct
            FROM work_cache
            WHERE namespace=? AND kind=? AND context_fingerprint=?
              AND (expires_at IS NULL OR expires_at>?)
            ORDER BY last_access DESC LIMIT 256
            """,
            (namespace, kind, context_fingerprint, now),
        ).fetchall()
        best = None
        best_score = -1.0
        for candidate in rows:
            score = self.vectorizer.cosine(query, json.loads(candidate[1]))
            if score > best_score:
                best_score = score
                best = candidate
        if best is None or best_score < reference_threshold:
            return CacheLookup("miss")
        self._touch(best[0])
        value = json.loads(best[2])
        can_direct = bool(best[3]) and bool(best[4]) and allow_semantic_direct
        if can_direct and best_score >= direct_threshold:
            return CacheLookup("exact", value, best_score, best[0])
        return CacheLookup("semantic_reference", value, best_score, best[0])

    def _touch(self, key: str) -> None:
        self.db.execute(
            "UPDATE work_cache SET hits=hits+1, last_access=? WHERE key=?",
            (_iso(_utcnow()), key),
        )
        self.db.commit()
