from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ExecutionHandle,
    ProcessReference,
    RecoveryState,
)
from codev_platform.reindex.attempts import AttemptJournalEntry
from codev_platform.reindex.posix_process import (
    POSIX_CONTAINMENT_KIND,
    LinuxProcessInfo,
    LinuxProcessScan,
    LinuxProcessTable,
    PosixAttemptProcessBackend,
    encode_posix_native_ref,
)


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux /proc 进程组后端只在 Linux/WSL 验证",
)

from tests import reindex_posix_process_support as support


def test_posix_recovery_same_boot_without_group_is_unconfirmed(tmp_path: Path) -> None:
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(backend, tmp_path, "success")
    while backend.poll(handle) is None:
        time.sleep(0.01)

    report = backend.recover(support._journal(handle), Deadline.start(0.1))
    direct = PosixAttemptProcessBackend(poll_interval=0.01).recover_handle(
        handle,
        Deadline.start(0.1),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert report.death_proof is None
    assert direct.state is RecoveryState.UNCONFIRMED


def test_posix_recovered_active_handle_remains_manageable(tmp_path: Path) -> None:
    original = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(original, tmp_path, "ignore-term", "--seconds", "10")
    support._wait_for_text(tmp_path / "ignore-term.log", "fixture stdout")
    recovered = PosixAttemptProcessBackend(poll_interval=0.01)
    journal_recovered = PosixAttemptProcessBackend(poll_interval=0.01)

    journal_report = journal_recovered.recover(support._journal(handle), Deadline.start(0.5))
    report = recovered.recover_handle(handle, Deadline.start(0.5))

    assert journal_report.state is RecoveryState.ACTIVE
    assert report.state is RecoveryState.ACTIVE
    assert report.handle == handle
    assert recovered.poll(handle) is None
    terminated = recovered.terminate(
        handle,
        grace_sec=0.01,
        deadline=Deadline.start(1.0),
    )
    assert terminated.forced is True
    assert terminated.confirmed_dead is False


def test_posix_empty_claimed_journal_is_safe_before_activation() -> None:
    backend = PosixAttemptProcessBackend()
    journal = AttemptJournalEntry(
        schema_version=1,
        owner_token="owner",
        claim_token="claim",
        attempt_id="attempt-crash-window",
        fence="fence",
        project_id="project",
        kind="chroma",
        spec_path="/tmp/spec.json",
        result_path="/tmp/result.json",
        pid=None,
        process_identity=None,
        containment_kind=None,
        native_ref=None,
        state="claimed",
        started_at=1_700_000_000.0,
        timeout_sec=10.0,
    )

    report = backend.recover(journal, Deadline.start(0.1))

    assert report.state is RecoveryState.NEVER_STARTED


def test_posix_reference_from_previous_boot_is_confirmed_dead(tmp_path: Path) -> None:
    boot_path = tmp_path / "boot_id"
    current_boot = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    previous_boot = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    boot_path.write_text(current_boot, encoding="ascii")
    table = LinuxProcessTable(boot_id_path=boot_path)
    native_ref = encode_posix_native_ref(previous_boot, 123, 123)
    identity = table.build_identity_from_values(
        pid=123,
        native_ref=native_ref,
        boot_id=previous_boot,
        start_ticks=456,
    )
    backend = PosixAttemptProcessBackend(
        process_table=table,
        wall_clock=lambda: 1_000_000_000.0,
    )

    proof = backend.confirm_reference_dead(
        ProcessReference(identity, POSIX_CONTAINMENT_KIND, native_ref),
        Deadline.start(0.1),
    )

    assert proof is not None
    assert "boot_id" in proof.evidence


def test_posix_recovery_previous_boot_returns_matching_handle_and_proof(
    tmp_path: Path,
) -> None:
    boot_path = tmp_path / "boot_id"
    current_boot = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    previous_boot = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    boot_path.write_text(current_boot, encoding="ascii")
    table = LinuxProcessTable(boot_id_path=boot_path)
    native_ref = encode_posix_native_ref(previous_boot, 123, 123)
    identity = table.build_identity_from_values(
        pid=123,
        native_ref=native_ref,
        boot_id=previous_boot,
        start_ticks=456,
    )
    handle = ExecutionHandle(
        "old-attempt",
        123,
        identity,
        POSIX_CONTAINMENT_KIND,
        native_ref,
        1_700_000_000.0,
    )
    backend = PosixAttemptProcessBackend(
        process_table=table,
        wall_clock=lambda: 1_000_000_000.0,
    )

    report = backend.recover(support._journal(handle), Deadline.start(0.1))
    direct = backend.recover_handle(handle, Deadline.start(0.1))

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == handle
    assert report.death_proof is not None
    assert report.death_proof.confirmed_at == handle.started_at
    assert direct.state is RecoveryState.CONFIRMED_DEAD
    assert direct.handle == handle


def test_posix_group_signal_rejects_unknown_reused_group_member(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = PosixAttemptProcessBackend()
    handle = support._start(backend, tmp_path, "success")
    while backend.poll(handle) is None:
        time.sleep(0.01)
    unknown = LinuxProcessInfo(
        pid=handle.pid + 100000,
        ppid=0,
        pgid=handle.pid,
        session_id=handle.pid,
        state="S",
        start_ticks=1,
    )
    monkeypatch.setattr(
        backend._table,
        "scan",
        lambda: LinuxProcessScan((unknown,), True),
    )
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))

    assert backend._signal_group(handle, signal.SIGKILL) is False
    assert signals == []


def test_posix_identity_failure_never_executes_target(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran.txt"

    class _UnreadableChildTable(LinuxProcessTable):
        def read(self, pid: int):
            raise OSError("proc read denied")

    backend = PosixAttemptProcessBackend(
        process_table=_UnreadableChildTable(),
        poll_interval=0.01,
    )

    with pytest.raises(AttemptProcessStartError):
        support._start(
            backend,
            tmp_path,
            "cgroup-probe",
            "--seconds",
            "0.1",
            "--state-path",
            str(marker),
        )

    time.sleep(0.15)
    assert not marker.exists()
