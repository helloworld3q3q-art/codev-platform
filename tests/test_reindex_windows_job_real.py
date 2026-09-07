"""Windows Job/STARTUPINFOEX 的真实内核与父崩溃回归。

原子 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 最低支持 Windows 10 / Server 2016。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.windows_job import WindowsJobAttemptProcessBackend
from codev_platform.reindex.windows_job_native import CtypesWindowsJobNative

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="仅 Windows 可验证真实 Job 内核语义"),
    pytest.mark.skipif(
        os.name == "nt" and sys.getwindowsversion()[:2] < (10, 0),
        reason="原子 JobList 要求 Windows 10 / Server 2016 及以上",
    ),
]


def test_real_windows_readiness_proves_job_control_plane() -> None:
    backend = WindowsJobAttemptProcessBackend(native=CtypesWindowsJobNative())

    backend.assert_ready(Deadline.start(3.0))


def test_real_windows_native_job_handle_is_not_inheritable() -> None:
    native = CtypesWindowsJobNative()
    name = f"Local\\codev-reindex-native-{os.getpid()}-{time.time_ns():x}"
    handle = native.create_job(name)
    try:
        native.configure_kill_on_close(handle)
        assert native.is_handle_inheritable(handle) is False
    finally:
        native.close_handle(handle)


def test_real_windows_job_runs_suspended_child_and_redirects_one_log(
    tmp_path: Path,
) -> None:
    backend = WindowsJobAttemptProcessBackend(native=CtypesWindowsJobNative())
    fixture = Path(__file__).parent / "fixtures" / "reindex_process_fixture.py"
    log_path = (tmp_path / "bootstrap.log").resolve()
    deadline = Deadline.start(5.0)
    handle = backend.prepare(
        attempt_id="real-windows-attempt",
        argv=(sys.executable, str(fixture), "success", "--seconds", "0"),
        cwd=tmp_path.resolve(),
        bootstrap_log=log_path,
        deadline=deadline,
    )
    try:
        time.sleep(0.1)
        assert backend.poll(handle) is None
        assert not log_path.exists() or log_path.read_bytes() == b""
        backend.activate(handle, deadline)
        end = time.monotonic() + 5.0
        while backend.poll(handle) is None and time.monotonic() < end:
            time.sleep(0.01)
        assert backend.poll(handle) == 0
        proof = backend.confirm_dead(handle, Deadline.start(2.0))
        assert proof is not None
        assert "ActiveProcesses=0" in proof.evidence
        assert log_path.read_text(encoding="utf-8") == "proof: success\n"
    finally:
        try:
            backend.terminate(handle, grace_sec=0, deadline=Deadline.start(2.0))
        except ValueError:
            pass


def test_windows_parent_crash_kills_job_grandchild_without_holding_outer_pipe(
    tmp_path: Path,
) -> None:
    marker = (tmp_path / "grandchild-survived.txt").resolve()
    ready = (tmp_path / "root-ready.txt").resolve()
    log_path = (tmp_path / "parent-crash.log").resolve()
    grandchild = (
        "import pathlib,time;time.sleep(0.8);"
        f"pathlib.Path({str(marker)!r}).write_text('alive',encoding='utf-8')"
    )
    root = "\n".join([
        "import os,pathlib,subprocess,sys,time",
        f"child=subprocess.Popen([sys.executable,'-c',{grandchild!r}])",
        f"pathlib.Path({str(ready)!r}).write_text(str(os.getpid())+':'+str(child.pid),encoding='ascii')",
        "time.sleep(2)",
    ])
    outer = "\n".join([
        "import os,sys,time",
        "from pathlib import Path",
        "from codev_platform.reindex.attempt_process import Deadline",
        "from codev_platform.reindex.windows_job import WindowsJobAttemptProcessBackend",
        "from codev_platform.reindex.windows_job_native import CtypesWindowsJobNative",
        "backend=WindowsJobAttemptProcessBackend(native=CtypesWindowsJobNative())",
        f"ready=Path({str(ready)!r})",
        "deadline=Deadline.start(5.0)",
        "handle=backend.prepare(attempt_id='parent-crash',argv=(sys.executable,'-c',sys.argv[1]),"
        f"cwd=Path.cwd(),bootstrap_log=Path({str(log_path)!r}),deadline=deadline)",
        "backend.activate(handle,deadline)",
        "end=time.monotonic()+2.0",
        "while not ready.exists() and time.monotonic()<end: time.sleep(0.01)",
        "assert ready.exists()",
        "print('outer-done',flush=True)",
        "os._exit(0)",
    ])

    completed = subprocess.run(
        [sys.executable, "-c", outer, root],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=True,
    )
    assert completed.stdout == "outer-done\n"
    time.sleep(1.2)
    assert not marker.exists()


def test_atomic_job_assignment_kills_suspended_root_on_prepare_parent_crash(
    tmp_path: Path,
) -> None:
    marker = (tmp_path / "suspended-root-ran.txt").resolve()
    ready = (tmp_path / "atomic-ready.txt").resolve()
    log_path = (tmp_path / "atomic-crash.log").resolve()
    attempt_id = f"atomic-parent-crash-{time.time_ns()}"
    target = f"from pathlib import Path;Path({str(marker)!r}).write_text('ran')"
    outer = "\n".join([
        "import os,sys",
        "from pathlib import Path",
        "from codev_platform.reindex.attempt_process import Deadline",
        "from codev_platform.reindex.windows_job_native import CtypesWindowsJobNative",
        "from codev_platform.reindex.windows_process_identity import windows_job_name_for_attempt",
        "native=CtypesWindowsJobNative()",
        "job=native.create_job(windows_job_name_for_attempt(sys.argv[2]))",
        "native.configure_kill_on_close(job)",
        "process=native.create_suspended((sys.executable,'-c',sys.argv[1]),Path.cwd(),"
        f"Path({str(log_path)!r}),Deadline.start(5.0),job)",
        f"Path({str(ready)!r}).write_text(str(process.pid)+'\\n'+process.birth_marker,encoding='ascii')",
        "print('atomic-ready',flush=True)",
        "os._exit(0)",
    ])

    completed = subprocess.run(
        [sys.executable, "-c", outer, target, attempt_id],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=3.0,
        check=True,
    )
    assert completed.stdout == "atomic-ready\n"
    pid_text, expected_birth = ready.read_text(encoding="ascii").splitlines()
    pid = int(pid_text)
    native = CtypesWindowsJobNative()
    deadline = time.monotonic() + 2.0
    original_gone = False
    while time.monotonic() < deadline:
        process_handle = native.open_process(pid)
        if process_handle is None:
            original_gone = True
            break
        try:
            if native.process_birth_marker(process_handle) != expected_birth:
                original_gone = True
                break
        finally:
            native.close_handle(process_handle)
        time.sleep(0.01)
    assert original_gone is True
    assert not marker.exists()
