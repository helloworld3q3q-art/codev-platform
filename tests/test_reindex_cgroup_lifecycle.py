from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    Deadline,
    ExecutionHandle,
    ProcessReference,
    RecoveryState,
)
from codev_platform.reindex.cgroup_process import (
    CGROUP_CONTAINMENT_KIND,
    CgroupAttemptProcessBackend,
    encode_cgroup_native_ref,
)
from codev_platform.reindex.posix_process_identity import (
    LinuxProcessInfo,
    LinuxProcessTable,
)

from tests import reindex_cgroup_process_support as support

def test_cgroup_assignment_wait_never_sleeps_with_negative_duration(
    tmp_path: Path,
) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    table = support._FakeProcessTable()
    moments = iter((100.0, 101.0, 101.1))
    sleeps: list[float] = []
    backend = CgroupAttemptProcessBackend(
        filesystem=fs,
        process_table=table,
        monotonic=lambda: next(moments),
        sleeper=sleeps.append,
    )

    class _RunningProcess:
        pid = 999

        @staticmethod
        def poll():
            return None

    assert (
        backend._preparer.wait_assignment(
            _RunningProcess(),
            encode_cgroup_native_ref(support._BOOT_ID, "negative-sleep"),
            Deadline(100.5),
        )
        is None
    )
    assert sleeps == []


def test_cgroup_term_then_kill_uses_monotonic_budget_and_epoch_proof(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)
    backend._live[handle.process_identity] = object()

    report = backend.terminate(
        handle,
        grace_sec=0.05,
        deadline=Deadline(clock.monotonic() + 0.5),
    )

    assert fs.kills == 1
    assert report.forced is True
    assert report.confirmed_dead is True
    assert report.requested_at >= 1_000_000_000
    assert report.death_proof is not None
    assert report.death_proof.confirmed_at >= 1_000_000_000
    assert report.finished_at <= 1_800_000_001.0
    assert handle.process_identity not in backend._live
    assert fs.removed == 1
    assert fs.present is False


def test_cgroup_death_proof_requires_populated_zero(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._StubbornCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.terminate(
        handle,
        grace_sec=0.01,
        deadline=Deadline(clock.monotonic() + 0.05),
    )

    assert fs.kills == 1
    assert report.forced is True
    assert report.confirmed_dead is False
    assert report.death_proof is None


@pytest.mark.parametrize("grace_sec", [float("nan"), float("inf")])
def test_cgroup_rejects_nonfinite_grace(
    tmp_path: Path,
    grace_sec: float,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)

    with pytest.raises(ValueError, match="grace"):
        backend.terminate(
            support._handle(fs, table),
            grace_sec=grace_sec,
            deadline=Deadline(clock.monotonic() + 0.1),
        )


def test_cgroup_recovery_root_exit_but_populated_is_active(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path, populated=True)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)
    table.info = LinuxProcessInfo(999, 1, 999, 999, "S", 999)

    report = backend.recover(
        support._journal(handle),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.ACTIVE
    assert report.handle == handle
    assert handle.process_identity in backend._live
    assert backend.poll(handle) is None


def test_cgroup_recover_handle_reuses_exact_active_identity(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path, populated=True)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.recover_handle(
        handle,
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.ACTIVE
    assert report.handle == handle


def test_cgroup_recover_handle_rejects_attempt_path_ambiguity(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path, populated=True)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    original = support._handle(fs, table)
    ambiguous = ExecutionHandle(
        "another-attempt",
        original.pid,
        original.process_identity,
        original.containment_kind,
        original.native_ref,
        original.started_at,
    )

    report = backend.recover_handle(
        ambiguous,
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert original.process_identity not in backend._live


def test_cgroup_missing_same_boot_is_confirmed_dead(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.recover(
        support._journal(handle),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == handle
    assert report.death_proof is not None


def test_cgroup_terminate_missing_path_uses_exact_evidence(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.terminate(
        handle,
        grace_sec=0.01,
        deadline=Deadline(clock.monotonic() + 0.1),
    )

    assert report.confirmed_dead is True
    assert report.death_proof is not None
    assert "路径已消失" in report.death_proof.evidence


def test_cgroup_handle_proofs_clamp_epoch_when_wall_clock_moves_backward(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    clock.wall_value = 1_000_000_000.0
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    recovery = backend.recover(support._journal(handle), Deadline(clock.monotonic() + 0.1))
    termination = backend.terminate(
        handle,
        grace_sec=0.01,
        deadline=Deadline(clock.monotonic() + 0.1),
    )
    confirmed = backend.confirm_dead(handle, Deadline(clock.monotonic() + 0.1))

    assert recovery.death_proof is not None
    assert recovery.death_proof.confirmed_at == handle.started_at
    assert termination.death_proof is not None
    assert termination.requested_at == handle.started_at
    assert termination.finished_at == handle.started_at
    assert termination.death_proof.confirmed_at == handle.started_at
    assert confirmed is not None
    assert confirmed.confirmed_at == handle.started_at


def test_cgroup_claimed_without_deterministic_path_is_never_started(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)

    report = backend.recover(
        support._claimed_journal(),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.NEVER_STARTED


def test_cgroup_claimed_with_deterministic_path_is_cleaned_before_retry(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    backend = support._backend(fs, support._FakeProcessTable(), clock)

    report = backend.recover(
        support._claimed_journal(),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.NEVER_STARTED
    assert fs.kills == 1
    assert fs.removed == 1


def test_cgroup_claimed_cleanup_failure_is_unconfirmed(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._StubbornCgroupFs(tmp_path)
    backend = support._backend(fs, support._FakeProcessTable(), clock)

    report = backend.recover(
        support._claimed_journal(),
        Deadline(clock.monotonic() + 0.05),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert fs.kills == 1
    assert fs.removed == 0


def test_cgroup_claimed_exists_io_failure_is_unconfirmed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    backend = support._backend(fs, support._FakeProcessTable(), clock)
    monkeypatch.setattr(
        fs,
        "exists_attempt",
        lambda _attempt_id: (_ for _ in ()).throw(PermissionError("denied")),
    )

    report = backend.recover(
        support._claimed_journal(),
        Deadline(clock.monotonic() + 0.05),
    )

    assert report.state is RecoveryState.UNCONFIRMED


def test_cgroup_recovery_populated_zero_returns_matching_handle_and_proof(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path, populated=False)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.recover(
        support._journal(handle),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == handle
    assert report.death_proof is not None
    assert fs.present is False


def test_cgroup_recover_handle_reports_exact_populated_zero_proof(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path, populated=False)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    report = backend.recover_handle(
        handle,
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == handle
    assert report.death_proof is not None
    assert report.state is not RecoveryState.NEVER_STARTED


def test_cgroup_previous_boot_reference_is_confirmed_dead(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    native_ref = encode_cgroup_native_ref(support._OLD_BOOT_ID, "old-attempt")
    identity = LinuxProcessTable.build_identity_from_values(
        pid=321,
        native_ref=native_ref,
        boot_id=support._OLD_BOOT_ID,
        start_ticks=456,
    )

    proof = backend.confirm_reference_dead(
        ProcessReference(identity, CGROUP_CONTAINMENT_KIND, native_ref),
        Deadline(clock.monotonic() + 0.1),
    )

    assert proof is not None
    assert "boot_id" in proof.evidence


def test_cgroup_missing_current_boot_reference_is_confirmed_dead(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    handle = support._handle(fs, table)

    proof = backend.confirm_reference_dead(
        ProcessReference(
            handle.process_identity,
            handle.containment_kind,
            handle.native_ref,
        ),
        Deadline(clock.monotonic() + 0.1),
    )

    assert proof is not None
    assert proof.process_identity == handle.process_identity


def test_cgroup_recovery_previous_boot_returns_matching_handle_and_proof(
    tmp_path: Path,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    native_ref = encode_cgroup_native_ref(support._OLD_BOOT_ID, "old-attempt")
    identity = LinuxProcessTable.build_identity_from_values(
        pid=321,
        native_ref=native_ref,
        boot_id=support._OLD_BOOT_ID,
        start_ticks=456,
    )
    handle = ExecutionHandle(
        "old-attempt",
        321,
        identity,
        CGROUP_CONTAINMENT_KIND,
        native_ref,
        1_700_000_000.0,
    )

    report = backend.recover(
        support._journal(handle),
        Deadline(clock.monotonic() + 0.1),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == handle
    assert report.death_proof is not None


def test_cgroup_terminate_previous_boot_uses_exact_evidence(tmp_path: Path) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    native_ref = encode_cgroup_native_ref(support._OLD_BOOT_ID, "old-terminate")
    identity = LinuxProcessTable.build_identity_from_values(
        pid=321,
        native_ref=native_ref,
        boot_id=support._OLD_BOOT_ID,
        start_ticks=456,
    )
    handle = ExecutionHandle(
        "old-terminate",
        321,
        identity,
        CGROUP_CONTAINMENT_KIND,
        native_ref,
        1_700_000_000.0,
    )

    report = backend.terminate(
        handle,
        grace_sec=0.01,
        deadline=Deadline(clock.monotonic() + 0.1),
    )

    assert report.death_proof is not None
    assert "boot_id" in report.death_proof.evidence
