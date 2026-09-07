"""Windows Job attempt 后端的契约与真实内核回归。"""
from __future__ import annotations

import dataclasses
import os
from collections import deque
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ProcessReference,
    RecoveryState,
)
from codev_platform.reindex.windows_job import (
    WindowsJobAttemptProcessBackend,
)

from tests import reindex_windows_job_support as support

def test_windows_termination_clamps_proof_and_report_after_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    native = support._Native()
    backend, handle, monotonic, _wall = support._started_before_wall_rollback(
        native,
        tmp_path,
    )
    native.active_processes = deque([0])

    report = backend.terminate(
        handle,
        grace_sec=0,
        deadline=Deadline(monotonic.now + 0.02),
    )

    assert report.death_proof is not None
    assert report.death_proof.confirmed_at == handle.started_at
    assert report.finished_at >= report.death_proof.confirmed_at


def test_windows_activation_failure_keeps_typed_error_after_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    native = support._Native()
    monotonic = support._Clock(10.0)
    wall = support._Clock(1_800_000_000.0)
    backend = support._backend(native, monotonic, wall)
    deadline = Deadline(monotonic.now + 1.0)
    handle = backend.prepare(
        attempt_id="activation-wall-rollback",
        argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
        cwd=tmp_path.resolve(),
        bootstrap_log=(tmp_path / "activation-wall-rollback.log").resolve(),
        deadline=deadline,
    )
    wall.now = 1_700_000_000.0
    native.resume_error = True

    with pytest.raises(AttemptProcessStartError) as caught:
        backend.activate(handle, deadline)

    assert caught.value.handle == handle
    assert caught.value.death_proof is not None
    assert caught.value.death_proof.confirmed_at == handle.started_at


def test_windows_recovery_clamps_job_absent_proof_after_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    original_native = support._Native()
    _backend_before, handle, monotonic, wall = support._started_before_wall_rollback(
        original_native,
        tmp_path,
    )
    recovered_native = support._Native()
    recovered_native.job_exists = False
    recovered_native.process_exists = False
    recovered = support._backend(recovered_native, monotonic, wall)

    report = recovered.recover(
        support._journal(handle),
        Deadline(monotonic.now + 0.02),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.death_proof is not None
    assert report.death_proof.confirmed_at == handle.started_at


def test_windows_confirm_dead_clamps_job_absent_proof_after_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    original_native = support._Native()
    _backend_before, handle, monotonic, wall = support._started_before_wall_rollback(
        original_native,
        tmp_path,
    )
    recovered_native = support._Native()
    recovered_native.job_exists = False
    recovered_native.process_exists = False
    recovered = support._backend(recovered_native, monotonic, wall)

    proof = recovered.confirm_dead(
        handle,
        Deadline(monotonic.now + 0.02),
    )

    assert proof is not None
    assert proof.confirmed_at == handle.started_at


def test_windows_process_reference_proof_keeps_observed_rollback_epoch(
    tmp_path: Path,
) -> None:
    original_native = support._Native()
    _backend_before, handle, monotonic, wall = support._started_before_wall_rollback(
        original_native,
        tmp_path,
    )
    reference = ProcessReference(
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
    )
    recovered_native = support._Native()
    recovered_native.job_exists = False
    recovered_native.process_exists = False
    recovered = support._backend(recovered_native, monotonic, wall)

    proof = recovered.confirm_reference_dead(
        reference,
        Deadline(monotonic.now + 0.02),
    )

    assert proof is not None
    assert proof.confirmed_at == wall.now


def test_windows_recover_empty_claimed_fields_queries_deterministic_job_name(
    tmp_path: Path,
) -> None:
    del tmp_path
    native = support._Native()
    native.job_exists = False
    clock = support._Clock()
    backend = WindowsJobAttemptProcessBackend(
        native=native,
        monotonic_clock=clock,
        wall_clock=clock,
        sleeper=clock.sleep,
        poll_interval_sec=0.01,
    )

    report = backend.recover(
        support._claimed_journal_without_process(),
        Deadline(clock.now + 0.01),
    )

    assert report.state is RecoveryState.NEVER_STARTED
    assert native.events == ["open_job"]


def test_windows_recover_empty_claimed_fields_terminates_unpublished_job(
    tmp_path: Path,
) -> None:
    del tmp_path
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)

    report = backend.recover(
        support._claimed_journal_without_process(),
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.NEVER_STARTED
    assert native.events == [
        "open_job",
        "terminate_job",
        "query_active_processes",
        "close_handle:201",
    ]


@pytest.mark.parametrize("failure_attr", ["terminate_error", "query_error"])
def test_windows_recover_empty_claimed_fields_keeps_native_ambiguity_unconfirmed(
    tmp_path: Path,
    failure_attr: str,
) -> None:
    del tmp_path
    native = support._Native()
    setattr(native, failure_attr, True)
    clock = support._Clock()

    report = support._backend(native, clock).recover(
        support._claimed_journal_without_process(),
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert native.events[-1] == "close_handle:201"


def test_windows_recover_reopens_matching_active_job(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    recovered = support._backend(recovered_native, clock)

    report = recovered.recover(support._journal(original), Deadline(clock.now + 0.02))

    assert report.state is RecoveryState.ACTIVE
    assert report.handle == original
    assert report.death_proof is None
    termination = recovered.terminate(
        original,
        grace_sec=0,
        deadline=Deadline(clock.now + 0.02),
    )
    assert termination.confirmed_dead is True


def test_windows_recover_handle_reopens_matching_active_job(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered = support._backend(support._Native(), clock)

    report = recovered.recover_handle(original, Deadline(clock.now + 0.02))

    assert report.state is RecoveryState.ACTIVE
    assert report.handle == original


def test_windows_recover_handle_rejects_attempt_job_ambiguity(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    ambiguous = dataclasses.replace(original, attempt_id="another-attempt")

    report = support._backend(recovered_native, clock).recover_handle(
        ambiguous,
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert recovered_native.events == []


def test_windows_recover_handle_rejects_in_memory_handle_collision(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    original = support._start(backend, tmp_path, clock)
    ambiguous = dataclasses.replace(original, started_at=original.started_at + 1)

    report = backend.recover_handle(ambiguous, Deadline(clock.now + 0.02))

    assert report.state is RecoveryState.UNCONFIRMED


def test_windows_recover_closes_opened_process_when_birth_query_fails(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    recovered_native.opened_birth_error = True

    report = support._backend(recovered_native, clock).recover(
        support._journal(original),
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.ACTIVE
    assert "close_handle:202" in recovered_native.events
