"""Windows Job attempt 后端的契约与真实内核回归。"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ProcessReference,
    RecoveryState,
)
from codev_platform.reindex.windows_job_native import (
    CtypesWindowsJobNative,
    NativeSuspendedProcess,
    WindowsJobNativeError,
)

from tests import reindex_windows_job_support as support

@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError])
def test_windows_native_create_error_after_process_creation_reclaims_every_handle(
    tmp_path: Path,
    monkeypatch,
    error_type: type[Exception],
) -> None:
    native = object.__new__(CtypesWindowsJobNative)
    closed: list[int] = []
    terminated: list[int] = []
    process = SimpleNamespace(hProcess=401, hThread=402)

    def fail_birth(_handle: int) -> str:
        raise error_type("出生标记替身失败")

    monkeypatch.setattr(native, "_open_stdio", lambda _path: (301, 302))
    monkeypatch.setattr(native, "_create_process", lambda *_args: process)
    monkeypatch.setattr(native, "process_birth_marker", fail_birth)
    monkeypatch.setattr(
        native,
        "_terminate_suspended_quietly",
        lambda handle, _deadline: terminated.append(handle),
    )
    monkeypatch.setattr(native, "_close_quietly", closed.append)

    with pytest.raises(error_type, match="出生标记"):
        native.create_suspended(
            (str(Path(os.sys.executable).resolve()), "-c", "pass"),
            tmp_path.resolve(),
            (tmp_path / "native-runtime-error.log").resolve(),
            Deadline.start(1.0),
            101,
        )

    assert terminated == [401]
    assert closed == [301, 302, 402, 401]


def test_windows_native_readiness_probe_uses_fixed_suspended_target_and_closes_stdio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = object.__new__(CtypesWindowsJobNative)
    closed: list[int] = []
    calls: list[tuple[tuple[str, ...], Path, int, int, int]] = []
    executable = (tmp_path / "System32" / "cmd.exe").resolve()
    process = SimpleNamespace(hProcess=401, hThread=402, dwProcessId=403)

    monkeypatch.setattr(
        native,
        "_system_probe_command",
        lambda: ((str(executable), "/d", "/c", "exit", "0"), executable.parent),
    )
    monkeypatch.setattr(native, "_open_readiness_stdio", lambda: (301, 302))
    monkeypatch.setattr(
        native,
        "_create_process",
        lambda argv, cwd, stdin, output, job: (
            calls.append((tuple(argv), cwd, stdin, output, job)) or process
        ),
    )
    monkeypatch.setattr(native, "process_birth_marker", lambda _handle: "windows-filetime:1")
    monkeypatch.setattr(native, "close_handle", closed.append)

    created = native.create_readiness_probe(Deadline.start(1.0), 101)

    assert calls == [
        ((str(executable), "/d", "/c", "exit", "0"), executable.parent, 301, 302, 101),
    ]
    assert created == NativeSuspendedProcess(403, 401, 402, "windows-filetime:1")
    assert closed == [301, 302]


def test_windows_native_readiness_stdio_cleanup_failure_reclaims_suspended_process(
    tmp_path: Path,
    monkeypatch,
) -> None:
    native = object.__new__(CtypesWindowsJobNative)
    closed: list[int] = []
    terminated: list[int] = []
    executable = (tmp_path / "System32" / "cmd.exe").resolve()
    process = SimpleNamespace(hProcess=401, hThread=402, dwProcessId=403)

    monkeypatch.setattr(
        native,
        "_system_probe_command",
        lambda: ((str(executable), "/d", "/c", "exit", "0"), executable.parent),
    )
    monkeypatch.setattr(native, "_open_readiness_stdio", lambda: (301, 302))
    monkeypatch.setattr(native, "_create_process", lambda *_args: process)
    monkeypatch.setattr(native, "process_birth_marker", lambda _handle: "windows-filetime:1")
    monkeypatch.setattr(
        native,
        "_terminate_suspended_quietly",
        lambda handle, _deadline: terminated.append(handle),
    )

    def fail_first_close(handle: int) -> None:
        closed.append(handle)
        if handle == 301:
            raise WindowsJobNativeError("CloseHandle", 5)

    monkeypatch.setattr(native, "close_handle", fail_first_close)

    with pytest.raises(WindowsJobNativeError, match="CloseHandle|readiness|清理"):
        native.create_readiness_probe(Deadline.start(1.0), 101)

    assert terminated == [401]
    assert closed == [301, 302, 402, 401]


def test_windows_native_readiness_output_open_failure_reports_input_cleanup_ambiguity(
    monkeypatch,
) -> None:
    native = object.__new__(CtypesWindowsJobNative)
    handles = iter((301, 0))
    closed: list[int] = []
    native._api = SimpleNamespace(create_file=lambda *_args: next(handles))

    def _fail_close(handle: int) -> None:
        closed.append(handle)
        raise WindowsJobNativeError("CloseHandle", 5)

    monkeypatch.setattr(native, "close_handle", _fail_close)

    with pytest.raises(WindowsJobNativeError, match="清理"):
        native._open_readiness_stdio()

    assert closed == [301]


def test_windows_start_registration_failure_preserves_existing_record(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    existing = support._start(backend, tmp_path, clock)

    with pytest.raises(AttemptProcessStartError):
        support._start(backend, tmp_path, clock)

    # 重复登记失败只能回收本次未转交资源，不能弹出已存在记录。
    assert backend.poll(existing) == 124
    assert {"close_handle:101", "close_handle:102", "close_handle:103"} <= set(native.events)


def test_windows_recover_confirmed_dead_keeps_exact_handle_and_proof(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    recovered_native.active_processes = deque([0])

    report = support._backend(recovered_native, clock).recover(
        support._journal(original),
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.CONFIRMED_DEAD
    assert report.handle == original
    assert report.death_proof is not None
    assert report.death_proof.process_identity == original.process_identity


def test_windows_recover_missing_job_keeps_matching_live_root_unconfirmed(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    recovered_native.job_exists = False
    recovered_native.process_rc = None

    report = support._backend(recovered_native, clock).recover(
        support._journal(original),
        Deadline(clock.now + 0.02),
    )

    assert report.state is RecoveryState.UNCONFIRMED
    assert report.handle is None
    assert report.death_proof is None


def test_windows_poll_without_root_handle_waits_for_job_to_be_empty(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    original = support._start(support._backend(native, clock), tmp_path, clock)
    recovered_native = support._Native()
    recovered_native.process_exists = False
    recovered_native.active_processes = deque([1])
    recovered = support._backend(recovered_native, clock)
    report = recovered.recover(support._journal(original), Deadline(clock.now + 0.02))
    assert report.handle is not None

    assert recovered.poll(report.handle) is None
    recovered_native.query_error = True
    assert recovered.poll(report.handle) is None
    recovered_native.query_error = False
    recovered_native.active_processes = deque([0])
    assert recovered.poll(report.handle) == 0


def test_windows_start_rejects_path_lookup_before_creating_job(tmp_path: Path) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)

    with pytest.raises(ValueError, match="argv|可执行|绝对"):
        backend.prepare(
            attempt_id="attempt-path-lookup",
            argv=("python", "-c", "pass"),
            cwd=tmp_path.resolve(),
            bootstrap_log=(tmp_path / "bootstrap.log").resolve(),
            deadline=Deadline(clock.now + 1.0),
        )

    assert native.events == []


def test_windows_recovery_rejects_reused_pid_without_terminating_it(
    tmp_path: Path,
) -> None:
    native = support._Native()
    clock = support._Clock()
    backend = support._backend(native, clock)
    handle = support._start(backend, tmp_path, clock)
    reference = ProcessReference(
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
    )

    # 模拟父进程重启：新后端没有内存句柄，Job 已因 KILL_ON_CLOSE 消失，PID 已复用。
    recovered_native = support._Native()
    recovered_native.job_exists = False
    recovered_native.opened_birth_marker = "windows-filetime:999999999"
    recovered = support._backend(recovered_native, clock)

    proof = recovered.confirm_reference_dead(
        reference,
        Deadline(clock.now + 0.02),
    )

    assert proof is not None
    assert "PID 已复用" in proof.evidence
    assert "terminate_process" not in recovered_native.events
