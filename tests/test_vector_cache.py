from pathlib import Path

from adaptive_harness.orchestration.vector_cache import SemanticWorkCache


def test_exact_verified_cache_reuse_is_context_scoped(tmp_path: Path):
    cache = SemanticWorkCache(tmp_path / "cache.sqlite3")
    cache.put(
        namespace="team-work",
        kind="code",
        semantic_text="inspect authentication middleware",
        context_fingerprint="repo-a",
        value={"answer": "A", "task_id": "t", "confidence": 0.9, "role": "cheap"},
        verified=True,
        allow_direct=True,
        ttl_seconds=60,
    )
    hit = cache.lookup(
        namespace="team-work",
        kind="code",
        semantic_text="inspect authentication middleware",
        context_fingerprint="repo-a",
    )
    assert hit.status == "exact"
    assert hit.value["answer"] == "A"

    miss = cache.lookup(
        namespace="team-work",
        kind="code",
        semantic_text="inspect authentication middleware",
        context_fingerprint="repo-b",
    )
    assert miss.status == "miss"


def test_unverified_exact_entry_is_reference_only(tmp_path: Path):
    cache = SemanticWorkCache(tmp_path / "cache.sqlite3")
    cache.put(
        namespace="team-work",
        kind="research",
        semantic_text="latest market price",
        context_fingerprint="ctx",
        value={"answer": "old"},
        verified=False,
        allow_direct=False,
        ttl_seconds=60,
    )
    hit = cache.lookup(
        namespace="team-work",
        kind="research",
        semantic_text="latest market price",
        context_fingerprint="ctx",
    )
    assert hit.status == "semantic_reference"
