from __future__ import annotations

import sys
import time
from pathlib import Path

from codev_platform.reindex.attempt_process import (
    Deadline,
)
from codev_platform.reindex.attempts import AttemptJournalEntry
from codev_platform.reindex.posix_process import (
    PosixAttemptProcessBackend,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "reindex_process_fixture.py"


def _wait_for_file(path: Path, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError("进程夹具状态文件未按时出现")


def _wait_for_pid(path: Path, timeout: float = 3.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            value = ""
        if value.isdigit() and int(value) > 0:
            return int(value)
        time.sleep(0.01)
    raise AssertionError("进程夹具 PID 未按时写完整")


def _wait_for_text(path: Path, pattern: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and pattern in path.read_text(encoding="utf-8"):
            return
        time.sleep(0.01)
    raise AssertionError("进程夹具日志未按时出现就绪标记")


def _journal(handle) -> AttemptJournalEntry:
    return AttemptJournalEntry(
        schema_version=1,
        owner_token="owner",
        claim_token="claim",
        attempt_id=handle.attempt_id,
        fence="fence",
        project_id="project",
        kind="chroma",
        spec_path="/tmp/spec.json",
        result_path="/tmp/result.json",
        pid=handle.pid,
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
        state="running",
        started_at=handle.started_at,
        timeout_sec=10.0,
    )


def _start(
    backend: PosixAttemptProcessBackend,
    tmp_path: Path,
    mode: str,
    *extra: str,
):
    handle = backend.prepare(
        attempt_id=f"attempt-{mode}",
        argv=[sys.executable, str(_FIXTURE), mode, *extra],
        cwd=tmp_path,
        bootstrap_log=tmp_path / f"{mode}.log",
        deadline=Deadline.start(3.0),
    )
    backend.activate(handle, Deadline.start(1.0))
    return handle
