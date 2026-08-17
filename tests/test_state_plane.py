from pathlib import Path

import pytest

from adaptive_harness.orchestration.contracts import VerificationCertificate, VerificationVerdict
from adaptive_harness.orchestration.state_plane import (
    FactStatus,
    MissionStatus,
    RevisionConflict,
    TaskStateStore,
    TaskStatus,
)


def _cert(
    strength: float = 0.9,
    verdict: VerificationVerdict = VerificationVerdict.VERIFIED,
):
    return VerificationCertificate(
        verdict=verdict,
        evidence_strength=strength,
        score=strength,
        deterministic=True,
        scope_coverage=0.95,
        evidence_refs=["call-1"],
        checks=["pytest"],
    )


def _store(tmp_path: Path) -> TaskStateStore:
    return TaskStateStore(tmp_path / "state.sqlite3", done_evidence_strength=0.6)


def test_external_key_recovers_same_mission(tmp_path: Path):
    store = _store(tmp_path)
    first = store.create_mission(goal="long audit", external_key="project:alpha")
    second = store.create_mission(
        goal="different ignored text", external_key="project:alpha"
    )
    assert first.id == second.id
    assert second.goal == "long audit"


def test_stale_revision_write_is_rejected(tmp_path: Path):
    store = _store(tmp_path)
    mission = store.create_mission(goal="build system")
    updated = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="inspect inputs",
        expected_revision=mission.revision,
    )
    assert updated.revision == 1
    with pytest.raises(RevisionConflict):
        store.add_task(
            mission_id=mission.id,
            task_id="b",
            description="stale writer",
            expected_revision=mission.revision,
        )


def test_dependency_must_finish_before_activation_or_finalization(tmp_path: Path):
    store = _store(tmp_path)
    mission = store.create_mission(goal="two-stage task")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="fix parser tests",
        expected_revision=mission.revision,
    )
    mission = store.add_task(
        mission_id=mission.id,
        task_id="b",
        description="validate integration tests",
        dependencies=["a"],
        expected_revision=mission.revision,
    )
    with pytest.raises(ValueError, match="dependency"):
        store.transition_task(
            mission_id=mission.id,
            task_id="b",
            target=TaskStatus.ACTIVE,
            expected_revision=mission.revision,
        )
    with pytest.raises(ValueError, match="dependency"):
        store.finalize_task(
            mission_id=mission.id,
            task_id="b",
            certificate=_cert(),
            output_summary="done",
            expected_revision=mission.revision,
        )


def test_done_requires_strong_certificate_and_direct_transition_is_forbidden(
    tmp_path: Path,
):
    store = _store(tmp_path)
    mission = store.create_mission(goal="verified work")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="fix parser tests",
        expected_revision=mission.revision,
    )
    with pytest.raises(ValueError, match="DONE requires"):
        store.transition_task(
            mission_id=mission.id,
            task_id="a",
            target=TaskStatus.DONE,
            expected_revision=mission.revision,
        )
    with pytest.raises(ValueError, match="evidence strength"):
        store.finalize_task(
            mission_id=mission.id,
            task_id="a",
            certificate=_cert(0.3, VerificationVerdict.SUPPORTED),
            output_summary="not enough",
            expected_revision=mission.revision,
        )


def test_verified_final_task_closes_mission_and_audit_is_clean(tmp_path: Path):
    store = _store(tmp_path)
    mission = store.create_mission(goal="verified work")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="fix parser tests",
        expected_revision=mission.revision,
    )
    mission = store.finalize_task(
        mission_id=mission.id,
        task_id="a",
        certificate=_cert(),
        output_summary="all parser tests pass",
        expected_revision=mission.revision,
    )
    assert mission.status == MissionStatus.DONE
    assert mission.tasks[0].status == TaskStatus.DONE
    assert mission.tasks[0].verification is not None
    assert store.audit(mission.id) == []
    assert [event["revision"] for event in store.events(mission.id)] == [0, 1, 2]


def test_ready_tasks_follow_dependency_graph(tmp_path: Path):
    store = _store(tmp_path)
    mission = store.create_mission(goal="dag")
    mission = store.add_task(
        mission_id=mission.id,
        task_id="a",
        description="fix parser tests",
        priority=0.4,
        expected_revision=mission.revision,
    )
    mission = store.add_task(
        mission_id=mission.id,
        task_id="b",
        description="independent check",
        priority=0.9,
        expected_revision=mission.revision,
    )
    mission = store.add_task(
        mission_id=mission.id,
        task_id="c",
        description="integration tests",
        dependencies=["a"],
        expected_revision=mission.revision,
    )
    assert [task.id for task in store.ready_tasks(mission.id)] == ["b", "a"]
    mission = store.finalize_task(
        mission_id=mission.id,
        task_id="a",
        certificate=_cert(),
        output_summary="a done",
        expected_revision=mission.revision,
    )
    assert {task.id for task in store.ready_tasks(mission.id)} == {"b", "c"}


def test_canonical_fact_requires_real_evidence_strength(tmp_path: Path):
    store = _store(tmp_path)
    mission = store.create_mission(goal="research")
    weak = VerificationCertificate(
        verdict=VerificationVerdict.UNVERIFIED,
        evidence_strength=0.08,
        evidence_refs=["call-x"],
    )
    with pytest.raises(ValueError, match="too weak"):
        store.record_fact(
            mission_id=mission.id,
            claim="claim",
            certificate=weak,
            expected_revision=mission.revision,
        )
    strong = VerificationCertificate(
        verdict=VerificationVerdict.SUPPORTED,
        evidence_strength=0.72,
        score=0.76,
        independent_sources=3,
        evidence_refs=["s1", "s2", "s3"],
    )
    mission = store.record_fact(
        mission_id=mission.id,
        claim="three independent sources support this",
        certificate=strong,
        expected_revision=mission.revision,
    )
    assert mission.facts[0].status == FactStatus.SUPPORTED
    assert mission.facts[0].evidence_strength == pytest.approx(0.72)
