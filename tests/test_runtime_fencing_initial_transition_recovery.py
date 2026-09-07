"""首次空 current 的 initial pending transition 崩溃收敛回归。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_fencing_store import (
    ControlLeaseStore,
    ControlLeaseStoreError,
)
from codev_platform.runtime_control_lease_persistence import ControlLeasePersistence
from codev_platform.runtime_recovery_contract import create_recovery_envelope
from codev_platform.runtime_storage import (
    control_lease_history_path,
    control_lease_pending_transition_path,
    control_lease_record_path,
)
from codev_platform.runtime_store_protocols import (
    RuntimeStorePolicy,
)
from codev_platform.runtime_transaction_contract import create_transaction_journal
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore
from tests.runtime_store_race_support import (
    ControlLeaseTransitionCrashInput,
    run_control_lease_transition_crash,
)


_POSIX = os.name == "posix"


def _reservation() -> AttemptReservation:
    """构造首次 initial 唯一接受的预租约身份。"""
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _bootstrap_without_current(tmp_path: Path) -> tuple[Path, AttemptReservation]:
    """持久化完整 bootstrap，但故意保持 control lease current 缺失。"""
    root = tmp_path / "runtime"
    root.mkdir()
    reservation = _reservation()
    journal = create_transaction_journal(reservation, created_at=reservation.created_at)
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=reservation.created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)

    assert not control_lease_record_path(root).exists()
    return root, reservation


def _pending_path(root: Path) -> Path:
    """返回固定 pending transition 叶子。"""
    return control_lease_pending_transition_path(root)


def _history_paths(root: Path) -> set[Path]:
    """收集所有 attempt 的 history，防止首次 initial 留下旧叶子。"""
    return {path.relative_to(root) for path in (root / "control-leases").glob("*/*.json")}


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("crash_stage", ("after_prepare", "after_freeze", "after_cas"))
def test首次空current初始签发崩溃后仅收敛唯一赢家(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    """O_EXCL current CAS 的三窗口只能完成 epoch=1 的首次 initial。"""
    root, reservation = _bootstrap_without_current(tmp_path)
    run_control_lease_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=None,
            crash_stage=crash_stage,
            operation="initial",
        ),
    )

    pending = _pending_path(root)
    assert pending.is_file()
    recovery_policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(recovery_policy)
    with recovery_policy.root_binding.bind() as bound_root:
        raw_current = persistence.load_current_or_none(bound_root)
    if crash_stage == "after_cas":
        assert raw_current is not None
        assert raw_current.record.epoch == 1
        assert raw_current.record.predecessor_sha256 is None
    else:
        assert raw_current is None

    recovery = ControlLeaseStore(recovery_policy)
    with pytest.raises(ControlLeaseStoreError, match="pending|未收敛"):
        recovery.load_current()

    winner = recovery.recover_pending_transition()

    assert winner is not None
    assert winner.record.attempt_id == reservation.attempt_id
    assert winner.record.epoch == 1
    assert winner.record.predecessor_sha256 is None
    assert recovery.load_current() == winner
    assert not pending.exists()
    assert _history_paths(root) == {
        control_lease_history_path(
            root,
            winner.record.attempt_id,
            winner.sha256,
        ).relative_to(root),
    }
