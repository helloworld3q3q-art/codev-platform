"""Windows Job attempt 后端的契约与真实内核回归。"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ProcessBackendReadinessError,
)
from codev_platform.reindex.windows_job import (
    WINDOWS_JOB_KIND,
    WindowsJobAttemptProcessBackend,
)
from codev_platform.reindex.windows_job_native import (
    NativeSuspendedProcess,
    WindowsJobNativeError,
)
from codev_platform.reindex.windows_process_identity import (
    WindowsJobReferenceData,
    decode_windows_job_reference,
    encode_windows_job_reference,
    windows_job_name_for_attempt,
)

from tests import reindex_windows_job_support as support

def test_windows_assigns_suspended_process_before_resume(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    deadline = Deadline(clock.now + 1.0)
    handle = backend.prepare(
        attempt_id="attempt-1",
        argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
        cwd=tmp_path.resolve(),
        bootstrap_log=(tmp_path / "bootstrap.log").resolve(),
        deadline=deadline,
    )

    assert "verify_process_in_job" in native.events
    assert "resume_primary_thread" not in native.events
    backend.activate(handle, deadline)

    ordered = [event.split(":", 1)[0] for event in native.events]
    assert ordered[:6] == [
        "create_job",
        "configure_kill_on_close",
        "is_handle_inheritable",
        "create_suspended",
        "verify_process_in_job",
        "resume_primary_thread",
    ]
    assert handle.pid == 4321
    assert handle.containment_kind == WINDOWS_JOB_KIND
    assert "windows-filetime" in handle.native_ref


def test_windows_readiness_proves_atomic_job_without_resuming_target() -> None:
    native = support._Native()
    clock = support._Clock()

    support._backend(native, clock).assert_ready(Deadline(clock.now + 1.0))

    ordered = [event.split(":", 1)[0] for event in native.events]
    assert ordered[:8] == [
        "create_job",
        "configure_kill_on_close",
        "is_handle_inheritable",
        "create_readiness_probe",
        "verify_process_in_job",
        "terminate_job",
        "poll_process",
        "query_active_processes",
    ]
    assert "resume_primary_thread" not in native.events
    assert ordered[-3:] == ["close_handle", "close_handle", "close_handle"]


def test_windows_readiness_verification_failure_cleans_all_probe_handles(
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()

    def fail_verify(_job_handle: int, _process_handle: int) -> None:
        native.events.append("verify_process_in_job")
        raise WindowsJobNativeError("IsProcessInJob", 5)

    monkeypatch.setattr(native, "verify_process_in_job", fail_verify)

    with pytest.raises(ProcessBackendReadinessError, match="Windows Job|readiness"):
        support._backend(native, clock).assert_ready(Deadline(clock.now + 1.0))

    assert "resume_primary_thread" not in native.events
    assert "terminate_job" in native.events
    assert "terminate_process" in native.events
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_readiness_cleanup_ambiguity_fails_after_attempting_every_close(
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original_close = native.close_handle

    def fail_one_close(handle: int) -> None:
        original_close(handle)
        if handle == 103:
            raise WindowsJobNativeError("CloseHandle", 5)

    monkeypatch.setattr(native, "close_handle", fail_one_close)

    with pytest.raises(ProcessBackendReadinessError, match="清理|readiness"):
        support._backend(native, clock).assert_ready(Deadline(clock.now + 1.0))

    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_readiness_deadline_crossing_cleans_suspended_probe(
    monkeypatch,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original_create = native.create_readiness_probe

    def cross_deadline(deadline: Deadline, job_handle: int) -> NativeSuspendedProcess:
        process = original_create(deadline, job_handle)
        clock.sleep(2.0)
        return process

    monkeypatch.setattr(native, "create_readiness_probe", cross_deadline)

    with pytest.raises(ProcessBackendReadinessError, match="预算|readiness"):
        support._backend(native, clock).assert_ready(Deadline(clock.now + 1.0))

    assert "resume_primary_thread" not in native.events
    assert "terminate_job" in native.events
    assert "poll_process" not in native.events
    assert "query_active_processes" not in native.events
    for handle in (101, 102, 103):
        assert native.events.count(f"close_handle:{handle}") == 1


def test_windows_persists_epoch_time_but_waits_on_monotonic_deadline(
    tmp_path: Path,
) -> None:
    native = support._Native()
    monotonic = support._Clock(10.0)
    wall_now = 1_800_000_000.0
    backend = WindowsJobAttemptProcessBackend(
        native=native,
        monotonic_clock=monotonic,
        wall_clock=lambda: wall_now + (monotonic.now - 10.0),
        sleeper=monotonic.sleep,
        poll_interval_sec=0.01,
    )

    handle = support._start(backend, tmp_path, monotonic)
    native.active_processes = deque([0])
    report = backend.terminate(
        handle,
        grace_sec=0,
        deadline=Deadline(monotonic.now + 0.02),
    )

    assert handle.started_at == wall_now
    assert report.requested_at == wall_now
    assert report.finished_at == wall_now
    assert report.death_proof is not None
    assert report.death_proof.confirmed_at == wall_now


def test_windows_native_reference_codec_is_strict_and_bounded() -> None:
    data = WindowsJobReferenceData(
        job_name="Local\\codev-reindex-codec0001",
        pid=4321,
        birth_marker="windows-filetime:123456789",
    )
    encoded = encode_windows_job_reference(data)

    assert decode_windows_job_reference(encoded) == data
    with pytest.raises(ValueError):
        decode_windows_job_reference(encoded[:-1] + ',"extra":1}')
    with pytest.raises(ValueError):
        decode_windows_job_reference(
            '{"birth_marker":"windows-filetime:1","job_name":'
            '"Local\\\\codev-reindex-codec0001","pid":1,"pid":2,"schema_version":1}'
        )

    first = windows_job_name_for_attempt("attempt-sensitive-name")
    assert first == windows_job_name_for_attempt("attempt-sensitive-name")
    assert first != windows_job_name_for_attempt("attempt-other")
    assert "attempt-sensitive-name" not in first


def test_windows_job_handle_must_be_non_inheritable(tmp_path: Path) -> None:
    native = support._Native()
    native.job_inheritable = True
    clock = support._Clock()

    with pytest.raises(AttemptProcessStartError) as caught:
        support._start(support._backend(native, clock), tmp_path, clock)

    assert caught.value.handle is None
    assert caught.value.retryable is True
    assert "create_suspended" not in native.events
    assert "close_handle:101" in native.events


def test_windows_assignment_failure_never_falls_back_to_raw_process(
    tmp_path: Path,
) -> None:
    native = support._Native()
    native.assign_error = True
    native.active_processes = deque([0])
    clock = support._Clock()

    with pytest.raises(AttemptProcessStartError) as caught:
        support._start(support._backend(native, clock), tmp_path, clock)

    assert caught.value.handle is not None
    assert caught.value.death_proof is not None
    assert caught.value.retryable is True
    assert "resume_primary_thread" not in native.events
    assert native.events.count("terminate_process") == 1


def test_windows_ambiguous_start_failure_preserves_handle_for_quarantine(
    tmp_path: Path,
) -> None:
    native = support._Native()
    native.assign_error = True
    native.terminate_error = True
    native.active_processes = deque([0])
    clock = support._Clock()
    backend = support._backend(native, clock)

    with pytest.raises(AttemptProcessStartError) as caught:
        support._start(backend, tmp_path, clock)

    assert caught.value.handle is not None
    assert caught.value.death_proof is None
    assert caught.value.retryable is False
    assert "close_handle:101" not in native.events
    assert "close_handle:102" not in native.events
    assert caught.value.handle is not None
    with pytest.raises(AttemptProcessStartError):
        backend.activate(caught.value.handle, Deadline(clock.now + 0.01))


def test_windows_resume_failure_kills_assigned_job_and_closes_all_handles(
    tmp_path: Path,
) -> None:
    native = support._Native()
    native.resume_error = True
    clock = support._Clock()

    with pytest.raises(AttemptProcessStartError) as caught:
        support._start(support._backend(native, clock), tmp_path, clock)

    assert caught.value.death_proof is not None
    assert caught.value.retryable is True
    assert native.events.count("terminate_job") == 1
    assert {"close_handle:101", "close_handle:102", "close_handle:103"} <= set(native.events)


def test_windows_confirmed_dead_requires_zero_active_job_processes(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    handle = support._start(backend, tmp_path, clock)

    native.active_processes = deque([1])
    assert backend.confirm_dead(handle, Deadline(clock.now + 0.02)) is None
    assert "close_handle:101" not in native.events

    native.active_processes = deque([1, 0])
    proof = backend.confirm_dead(handle, Deadline(clock.now + 0.02))
    assert proof is not None
    assert proof.process_identity == handle.process_identity
    assert "ActiveProcesses=0" in proof.evidence
    assert "close_handle:101" in native.events


def test_windows_terminate_waits_grace_then_forces_job(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    handle = support._start(backend, tmp_path, clock)
    native.active_processes = deque([1])

    report = backend.terminate(
        handle,
        grace_sec=0.02,
        deadline=Deadline(clock.now + 0.06),
    )

    assert report.graceful is False
    assert report.forced is True
    assert report.confirmed_dead is True
    assert native.events.count("terminate_job") == 1
    assert report.finished_at <= 10.06


def test_windows_terminate_reports_natural_job_exit_without_force(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    handle = support._start(backend, tmp_path, clock)
    native.active_processes = deque([1, 0])

    report = backend.terminate(
        handle,
        grace_sec=0.02,
        deadline=Deadline(clock.now + 0.05),
    )

    assert report.graceful is True
    assert report.forced is False
    assert report.confirmed_dead is True
    assert "terminate_job" not in native.events
