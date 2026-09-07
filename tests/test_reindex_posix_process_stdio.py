from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

from codev_platform.reindex import posix_process
from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
)
from codev_platform.reindex.posix_process import (
    PosixAttemptProcessBackend,
)
from codev_platform.reindex.process_stdio import open_direct_log


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux /proc 进程组后端只在 Linux/WSL 验证",
)

from tests import reindex_posix_process_support as support


def test_direct_log_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "bootstrap.fifo"
    os.mkfifo(fifo)
    results: list[object] = []

    def _open() -> None:
        try:
            stream = open_direct_log(fifo)
        except OSError as exc:
            results.append(exc)
            return
        results.append(stream)
        stream.close()

    worker = threading.Thread(target=_open, daemon=True)
    worker.start()
    worker.join(0.3)
    blocked = worker.is_alive()
    reader = None
    if blocked:
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        worker.join(1.0)
    if reader is not None:
        os.close(reader)

    assert blocked is False
    assert len(results) == 1
    assert isinstance(results[0], OSError)


def test_direct_log_rejects_device_and_socket(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        open_direct_log(Path("/dev/null"))

    socket_path = tmp_path / "bootstrap.sock"
    listener = socket.socket(socket.AF_UNIX)
    try:
        listener.bind(str(socket_path))
        with pytest.raises(OSError):
            open_direct_log(socket_path)
    finally:
        listener.close()


def test_posix_stdout_and_stderr_are_direct_files(tmp_path: Path) -> None:
    backend = PosixAttemptProcessBackend()
    handle = support._start(backend, tmp_path, "ignore-term", "--seconds", "10")
    support._wait_for_text(tmp_path / "ignore-term.log", "fixture stdout")
    stdout_target = Path(f"/proc/{handle.pid}/fd/1").resolve()
    stderr_target = Path(f"/proc/{handle.pid}/fd/2").resolve()

    try:
        assert stdout_target == (tmp_path / "ignore-term.log").resolve()
        assert stderr_target == stdout_target
    finally:
        backend.terminate(handle, grace_sec=0.01, deadline=Deadline.start(1.0))


def test_posix_log_open_failure_is_structured(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = PosixAttemptProcessBackend()

    def _fail_log(_path: Path):
        raise OSError("disk full")

    monkeypatch.setattr(posix_process, "open_direct_log", _fail_log)

    with pytest.raises(AttemptProcessStartError, match="日志"):
        backend.prepare(
            attempt_id="log-failure",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "bootstrap.log",
            deadline=Deadline.start(1.0),
        )
