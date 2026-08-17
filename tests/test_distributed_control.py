from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaptive_harness.orchestration.distributed_control import (
    BudgetExceeded,
    DistributedControlStore,
    EscrowStatus,
    LeaseBusy,
    LeaseLost,
    LeaseStatus,
)


def _store(tmp_path: Path) -> DistributedControlStore:
    return DistributedControlStore(tmp_path / "distributed.sqlite3")


def _t0() -> datetime:
    return datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def test_task_lease_is_exclusive_then_recoverable_after_expiry(tmp_path: Path):
    store = _store(tmp_path)
    lease = store.acquire_task(
        mission_id="m1", task_id="t1", owner="cell-a", ttl_seconds=10, now=_t0()
    )
    assert lease.status == LeaseStatus.ACTIVE
    with pytest.raises(LeaseBusy):
        store.acquire_task(
            mission_id="m1", task_id="t1", owner="cell-b", ttl_seconds=10, now=_t0()
        )

    recovered = store.acquire_task(
        mission_id="m1",
        task_id="t1",
        owner="cell-b",
        ttl_seconds=10,
        now=_t0() + timedelta(seconds=11),
    )
    assert recovered.owner == "cell-b"
    assert recovered.lease_id != lease.lease_id
    with pytest.raises(LeaseLost):
        store.finish_lease(
            lease.lease_id,
            owner="cell-a",
            completed=True,
            now=_t0() + timedelta(seconds=12),
        )


def test_heartbeat_extends_lease_and_wrong_owner_cannot_finish(tmp_path: Path):
    store = _store(tmp_path)
    lease = store.acquire_task(
        mission_id="m", task_id="t", owner="a", ttl_seconds=10, now=_t0()
    )
    beat = store.heartbeat(
        lease.lease_id,
        owner="a",
        ttl_seconds=20,
        now=_t0() + timedelta(seconds=8),
    )
    assert beat.expires_at == _t0() + timedelta(seconds=28)
    with pytest.raises(LeaseLost):
        store.finish_lease(
            lease.lease_id,
            owner="b",
            completed=True,
            now=_t0() + timedelta(seconds=9),
        )
    finished = store.finish_lease(
        lease.lease_id,
        owner="a",
        completed=True,
        outcome="verified",
        now=_t0() + timedelta(seconds=9),
    )
    assert finished.status == LeaseStatus.COMPLETED
    assert finished.outcome == "verified"


def test_sibling_escrows_cannot_overallocate_parent(tmp_path: Path):
    store = _store(tmp_path)
    root = store.create_root_escrow(scope="mission:m", owner="manager", allocated_usd=1.0)
    a = store.allocate_child_escrow(
        root.escrow_id, scope="cell:a", owner="a", allocated_usd=0.65
    )
    assert a.allocated_usd == pytest.approx(0.65)
    assert store.escrow(root.escrow_id).available_usd == pytest.approx(0.35)
    with pytest.raises(BudgetExceeded):
        store.allocate_child_escrow(
            root.escrow_id, scope="cell:b", owner="b", allocated_usd=0.36
        )


def test_child_settlement_charges_actual_spend_and_returns_unused_reservation(tmp_path: Path):
    store = _store(tmp_path)
    root = store.create_root_escrow(scope="mission", owner="manager", allocated_usd=1.0)
    child = store.allocate_child_escrow(
        root.escrow_id, scope="cell", owner="worker", allocated_usd=0.6
    )
    store.charge(child.escrow_id, 0.2)
    before = store.escrow(root.escrow_id)
    assert before.reserved_usd == pytest.approx(0.6)
    assert before.spent_usd == pytest.approx(0.0)

    settled = store.settle_escrow(child.escrow_id)
    assert settled.status == EscrowStatus.SETTLED
    after = store.escrow(root.escrow_id)
    assert after.spent_usd == pytest.approx(0.2)
    assert after.reserved_usd == pytest.approx(0.0)
    assert after.available_usd == pytest.approx(0.8)


def test_nested_escrow_propagates_only_real_spend_upward(tmp_path: Path):
    store = _store(tmp_path)
    root = store.create_root_escrow(scope="root", owner="root", allocated_usd=2.0)
    cell = store.allocate_child_escrow(
        root.escrow_id, scope="cell", owner="lead", allocated_usd=1.0
    )
    leaf = store.allocate_child_escrow(
        cell.escrow_id, scope="leaf", owner="worker", allocated_usd=0.4
    )
    store.charge(leaf.escrow_id, 0.15)
    store.settle_escrow(leaf.escrow_id)
    assert store.escrow(cell.escrow_id).spent_usd == pytest.approx(0.15)
    store.charge(cell.escrow_id, 0.10)
    store.settle_escrow(cell.escrow_id)
    assert store.escrow(root.escrow_id).spent_usd == pytest.approx(0.25)
    assert store.escrow(root.escrow_id).available_usd == pytest.approx(1.75)


def test_cancelled_unused_child_releases_entire_reservation(tmp_path: Path):
    store = _store(tmp_path)
    root = store.create_root_escrow(scope="root", owner="root", allocated_usd=0.5)
    child = store.allocate_child_escrow(
        root.escrow_id, scope="child", owner="worker", allocated_usd=0.4
    )
    cancelled = store.cancel_escrow(child.escrow_id)
    assert cancelled.status == EscrowStatus.CANCELLED
    root_now = store.escrow(root.escrow_id)
    assert root_now.available_usd == pytest.approx(0.5)
    assert root_now.spent_usd == pytest.approx(0.0)
