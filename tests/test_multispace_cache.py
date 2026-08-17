from pathlib import Path

from adaptive_harness.orchestration.multispace_cache import MultiSpaceSemanticWorkCache


def _cache(tmp_path: Path) -> MultiSpaceSemanticWorkCache:
    return MultiSpaceSemanticWorkCache(
        tmp_path / "cache.sqlite3",
        calibration_min_samples=4,
        precision_target=0.99,
        entity_overlap_direct=0.9,
        procedure_floor_direct=0.5,
    )


def test_entity_guard_blocks_semantic_direct_reuse_for_different_targets(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        namespace="team-work",
        kind="code",
        semantic_text="audit src/payments.py endpoint /v1/payments/123 for SQL injection",
        context_fingerprint="workspace",
        value={"answer": "checked target A"},
        verified=True,
        allow_direct=True,
        ttl_seconds=3600,
    )
    hit = cache.lookup(
        namespace="team-work",
        kind="code",
        semantic_text="audit src/users.py endpoint /v1/users/999 for SQL injection",
        context_fingerprint="workspace",
        direct_threshold=0.45,
        reference_threshold=0.20,
    )
    assert hit.status == "semantic_reference"
    assert hit.value == {"answer": "checked target A"}


def test_close_paraphrase_with_same_material_entities_can_reuse(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        namespace="team-work",
        kind="code",
        semantic_text="review src/payments.py /v1/payments/123 for SQL injection vulnerability",
        context_fingerprint="workspace",
        value={"answer": "verified result"},
        verified=True,
        allow_direct=True,
        ttl_seconds=3600,
    )
    hit = cache.lookup(
        namespace="team-work",
        kind="code",
        semantic_text="audit SQL injection risk in src/payments.py at /v1/payments/123",
        context_fingerprint="workspace",
        direct_threshold=0.35,
        reference_threshold=0.20,
    )
    assert hit.status == "exact"
    assert hit.value == {"answer": "verified result"}


def test_negative_feedback_tightens_direct_threshold(tmp_path: Path):
    cache = _cache(tmp_path)
    before = cache._effective_direct_threshold("team-work", "research", 0.90)
    for _ in range(4):
        cache.record_feedback(namespace="team-work", kind="research", accepted=False)
    after = cache._effective_direct_threshold("team-work", "research", 0.90)
    assert after > before


def test_workspace_fingerprint_still_isolates_cache(tmp_path: Path):
    cache = _cache(tmp_path)
    cache.put(
        namespace="team-work",
        kind="generic",
        semantic_text="same task",
        context_fingerprint="A",
        value={"answer": "A"},
        verified=True,
        allow_direct=True,
        ttl_seconds=3600,
    )
    hit = cache.lookup(
        namespace="team-work",
        kind="generic",
        semantic_text="same task",
        context_fingerprint="B",
        direct_threshold=0.2,
        reference_threshold=0.1,
    )
    assert hit.status == "miss"
