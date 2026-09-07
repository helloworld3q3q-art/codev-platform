"""Windows Job attempt 后端的契约与真实内核回归。"""
from __future__ import annotations

import os
from collections import deque
from pathlib import Path


from codev_platform.reindex.attempt_process import (
    Deadline,
    ExecutionHandle,
)
from codev_platform.reindex.attempts import AttemptJournalEntry
from codev_platform.reindex.windows_job import (
    WindowsJobAttemptProcessBackend,
)
from codev_platform.reindex.windows_job_native import (
    NativeSuspendedProcess,
    WindowsJobNativeError,
)

class _Clock:
    def __init__(self, now: float = 10.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _Native:
    """只模拟 Win32 叶子调用，不复制后端状态机。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.active_processes: deque[int] = deque([1])
        self.process_rc: int | None = None
        self.birth_marker = "windows-filetime:123456789"
        self.opened_birth_marker: str | None = self.birth_marker
        self.job_exists = True
        self.process_exists = True
        self.assign_error = False
        self.resume_error = False
        self.terminate_error = False
        self.job_inheritable = False
        self.opened_birth_error = False
        self.query_error = False

    def create_job(self, name: str) -> int:
        self.events.append(f"create_job:{name}")
        return 101

    def configure_kill_on_close(self, job_handle: int) -> None:
        assert job_handle == 101
        self.events.append("configure_kill_on_close")

    def is_handle_inheritable(self, handle: int) -> bool:
        assert handle == 101
        self.events.append("is_handle_inheritable")
        return self.job_inheritable

    def create_suspended(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        bootstrap_log: Path,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess:
        assert argv
        assert cwd.is_absolute()
        assert bootstrap_log.is_absolute()
        assert isinstance(deadline, Deadline)
        assert job_handle == 101
        self.events.append("create_suspended")
        return NativeSuspendedProcess(
            pid=4321,
            process_handle=102,
            thread_handle=103,
            birth_marker=self.birth_marker,
        )

    def create_readiness_probe(
        self,
        deadline: Deadline,
        job_handle: int,
    ) -> NativeSuspendedProcess:
        assert isinstance(deadline, Deadline)
        assert job_handle == 101
        self.events.append("create_readiness_probe")
        return NativeSuspendedProcess(
            pid=4321,
            process_handle=102,
            thread_handle=103,
            birth_marker=self.birth_marker,
        )

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        assert (job_handle, process_handle) == (101, 102)
        self.events.append("assign_process")
        if self.assign_error:
            raise WindowsJobNativeError("AssignProcessToJobObject", 5)

    def verify_process_in_job(self, job_handle: int, process_handle: int) -> None:
        assert (job_handle, process_handle) == (101, 102)
        self.events.append("verify_process_in_job")
        if self.assign_error:
            raise WindowsJobNativeError("IsProcessInJob", 5)

    def resume_primary_thread(self, thread_handle: int) -> None:
        assert thread_handle == 103
        self.events.append("resume_primary_thread")
        if self.resume_error:
            raise WindowsJobNativeError("ResumeThread", 5)

    def poll_process(self, process_handle: int) -> int | None:
        assert process_handle in (102, 202)
        self.events.append("poll_process")
        return self.process_rc

    def query_active_processes(self, job_handle: int) -> int:
        assert job_handle in (101, 201)
        self.events.append("query_active_processes")
        if self.query_error:
            raise WindowsJobNativeError("QueryInformationJobObject", 5)
        value = self.active_processes[0]
        if len(self.active_processes) > 1:
            self.active_processes.popleft()
        return value

    def terminate_job(self, job_handle: int, exit_code: int) -> None:
        assert job_handle in (101, 201)
        assert exit_code > 0
        self.events.append("terminate_job")
        if self.terminate_error:
            raise WindowsJobNativeError("TerminateJobObject", 5)
        self.active_processes = deque([0])
        self.process_rc = exit_code

    def terminate_process(self, process_handle: int, exit_code: int) -> None:
        assert process_handle == 102
        assert exit_code > 0
        self.events.append("terminate_process")
        if self.terminate_error:
            raise WindowsJobNativeError("TerminateProcess", 5)
        self.process_rc = exit_code

    def open_job(self, name: str) -> int | None:
        assert name.startswith("Local\\codev-reindex-")
        self.events.append("open_job")
        return 201 if self.job_exists else None

    def open_process(self, pid: int) -> int | None:
        assert pid == 4321
        self.events.append("open_process")
        return 202 if self.process_exists else None

    def process_birth_marker(self, process_handle: int) -> str:
        assert process_handle in (102, 202)
        self.events.append("process_birth_marker")
        if process_handle == 202 and self.opened_birth_error:
            raise WindowsJobNativeError("GetProcessTimes", 5)
        assert self.opened_birth_marker is not None
        return self.opened_birth_marker

    def close_handle(self, handle: int) -> None:
        self.events.append(f"close_handle:{handle}")


def _backend(
    native: _Native,
    clock: _Clock,
    wall_clock: _Clock | None = None,
) -> WindowsJobAttemptProcessBackend:
    return WindowsJobAttemptProcessBackend(
        native=native,
        monotonic_clock=clock,
        wall_clock=wall_clock or clock,
        sleeper=clock.sleep,
        poll_interval_sec=0.01,
    )


def _start(
    backend: WindowsJobAttemptProcessBackend,
    tmp_path: Path,
    clock: _Clock,
):
    deadline = Deadline(clock.now + 1.0)
    handle = backend.prepare(
        attempt_id="attempt-1",
        argv=(str(Path(os.sys.executable).resolve()), "-c", "pass"),
        cwd=tmp_path.resolve(),
        bootstrap_log=(tmp_path / "bootstrap.log").resolve(),
        deadline=deadline,
    )
    backend.activate(handle, deadline)
    return handle

def _journal(handle) -> AttemptJournalEntry:
    return AttemptJournalEntry(
        schema_version=1,
        owner_token="owner-1",
        claim_token="claim-1",
        attempt_id=handle.attempt_id,
        fence="fence-1",
        project_id="project-1",
        kind="chroma",
        spec_path="spec.json",
        result_path="result.json",
        pid=handle.pid,
        process_identity=handle.process_identity,
        containment_kind=handle.containment_kind,
        native_ref=handle.native_ref,
        state="running",
        started_at=handle.started_at,
        timeout_sec=30.0,
    )


def _claimed_journal_without_process() -> AttemptJournalEntry:
    return AttemptJournalEntry(
        schema_version=1,
        owner_token="owner-1",
        claim_token="claim-1",
        attempt_id="attempt-before-running-fsync",
        fence="fence-1",
        project_id="project-1",
        kind="chroma",
        spec_path="spec.json",
        result_path="result.json",
        pid=None,
        process_identity=None,
        containment_kind=None,
        native_ref=None,
        state="claimed",
        started_at=1_800_000_000.0,
        timeout_sec=30.0,
    )


def _started_before_wall_rollback(
    native: _Native,
    tmp_path: Path,
) -> tuple[WindowsJobAttemptProcessBackend, ExecutionHandle, _Clock, _Clock]:
    monotonic = _Clock(10.0)
    wall = _Clock(1_800_000_000.0)
    backend = _backend(native, monotonic, wall)
    handle = _start(backend, tmp_path, monotonic)
    wall.now = 1_700_000_000.0
    return backend, handle, monotonic, wall
