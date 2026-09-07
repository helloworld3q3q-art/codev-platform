from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    Deadline,
)
from codev_platform.reindex.posix_process import (
    PosixAttemptProcessBackend,
)


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux /proc 进程组后端只在 Linux/WSL 验证",
)

from tests import reindex_posix_process_support as support


def test_posix_term_then_kill_stays_inside_deadline(tmp_path: Path) -> None:
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(backend, tmp_path, "ignore-term", "--seconds", "10")
    support._wait_for_text(tmp_path / "ignore-term.log", "fixture stdout")
    started = time.monotonic()

    report = backend.terminate(
        handle,
        grace_sec=0.08,
        deadline=Deadline.start(0.8),
    )

    assert time.monotonic() - started < 1.3
    assert report.forced is True
    assert report.confirmed_dead is False
    assert report.death_proof is None
    assert handle.process_identity in backend._live


def test_activated_posix_graceful_exit_remains_unconfirmed(tmp_path: Path) -> None:
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(backend, tmp_path, "term-exit", "--seconds", "10")

    report = backend.terminate(
        handle,
        grace_sec=0.5,
        deadline=Deadline.start(1.0),
    )

    assert report.graceful is False
    assert report.forced is False
    assert report.confirmed_dead is False


@pytest.mark.parametrize("grace_sec", [float("nan"), float("inf")])
def test_posix_rejects_nonfinite_grace(tmp_path: Path, grace_sec: float) -> None:
    backend = PosixAttemptProcessBackend()
    handle = support._start(backend, tmp_path, "success")

    with pytest.raises(ValueError, match="grace"):
        backend.terminate(handle, grace_sec=grace_sec, deadline=Deadline.start(1.0))


def test_posix_backend_accepts_absolute_non_python_target(tmp_path: Path) -> None:
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = backend.prepare(
        attempt_id="non-python-target",
        argv=["/bin/sh", "-c", "sleep 0.05"],
        cwd=tmp_path,
        bootstrap_log=tmp_path / "non-python.log",
        deadline=Deadline.start(2.0),
    )
    backend.activate(handle, Deadline.start(1.0))
    deadline = time.monotonic() + 2.0
    rc = backend.poll(handle)
    while rc is None and time.monotonic() < deadline:
        time.sleep(0.01)
        rc = backend.poll(handle)

    assert rc == 0


def test_posix_group_kill_removes_grandchild(tmp_path: Path) -> None:
    pid_path = tmp_path / "grandchild.pid"
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(
        backend,
        tmp_path,
        "group-grandchild",
        "--seconds",
        "10",
        "--state-path",
        str(pid_path),
    )
    support._wait_for_file(pid_path)
    grandchild_pid = support._wait_for_pid(pid_path)

    report = backend.terminate(
        handle,
        grace_sec=0.05,
        deadline=Deadline.start(1.0),
    )

    assert report.confirmed_dead is False
    assert report.forced is True
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{grandchild_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{grandchild_pid}").exists()


def test_posix_setsid_escape_is_permanently_unconfirmed(tmp_path: Path) -> None:
    pid_path = tmp_path / "escaped.pid"
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(
        backend,
        tmp_path,
        "setsid-grandchild",
        "--seconds",
        "10",
        "--state-path",
        str(pid_path),
    )
    support._wait_for_file(pid_path)
    escaped_pid = support._wait_for_pid(pid_path)

    try:
        report = backend.terminate(
            handle,
            grace_sec=0.05,
            deadline=Deadline.start(1.0),
        )
        assert report.confirmed_dead is False
        assert report.death_proof is None
        assert backend.confirm_dead(handle, Deadline.start(0.1)) is None
    finally:
        try:
            os.kill(escaped_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_posix_fast_reparented_setsid_escape_never_gets_strict_proof(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "fast-escaped.pid"
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = support._start(
        backend,
        tmp_path,
        "fast-setsid-orphan",
        "--seconds",
        "10",
        "--state-path",
        str(pid_path),
    )
    support._wait_for_file(pid_path)
    escaped_pid = support._wait_for_pid(pid_path)
    deadline = time.monotonic() + 2.0
    while backend.poll(handle) is None and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        assert backend.confirm_dead(handle, Deadline.start(0.1)) is None
    finally:
        try:
            os.kill(escaped_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
