from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    Deadline,
    RecoveryState,
)
from codev_platform.reindex.cgroup_process import (
    CgroupAttemptProcessBackend,
    CgroupDelegationError,
    SystemdCgroupV2,
)

from tests import reindex_cgroup_process_support as support

@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="cgroup bootstrap 只在 Linux/WSL 验证",
)
def test_cgroup_bootstrap_assignment_failure_never_execs_target(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    target = root / ("attempt-" + "a" * 64)
    target.mkdir(parents=True)
    (target / "cgroup.procs").mkdir()
    marker = tmp_path / "target-ran.txt"
    bootstrap = (
        Path(__file__).parents[1] / "codev_platform" / "reindex" / "cgroup_bootstrap.py"
    ).resolve()
    env = dict(os.environ)
    env["CODEV_REINDEX_CGROUP_ROOT"] = str(root)
    command = [
        sys.executable,
        str(bootstrap),
        "--cgroup",
        str(target),
        "--ready-fd",
        "9",
        "--expected-parent-pid",
        str(os.getpid()),
        "--",
        sys.executable,
        str(support._FIXTURE),
        "cgroup-probe",
        "--state-path",
        str(marker),
    ]

    completed = subprocess.run(command, env=env, check=False, timeout=3)

    assert completed.returncode == 125
    assert not marker.exists()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="systemd delegated cgroup 只在 Linux/WSL 验证",
)
def test_cgroup_backend_fails_closed_without_delegation() -> None:
    current = Path("/proc/self/cgroup").read_text(encoding="ascii").strip()
    relative = current.removeprefix("0::").lstrip("/")
    root = Path("/sys/fs/cgroup") / relative
    try:
        delegated = os.getxattr(root, "user.delegate") == b"1"
    except OSError:
        delegated = False
    if delegated:
        pytest.skip("当前测试进程已有 systemd delegation，由真实正向用例覆盖")

    with pytest.raises(CgroupDelegationError):
        SystemdCgroupV2()


@pytest.mark.skipif(
    os.environ.get("CODEV_REINDEX_REAL_CGROUP") != "1",
    reason="需在 Delegate=yes 的临时 systemd unit 内显式运行",
)
def test_real_cgroup_readiness_proves_delegated_kill_control() -> None:
    backend = CgroupAttemptProcessBackend(poll_interval=0.01)

    backend.assert_ready(Deadline.start(3.0))


@pytest.mark.skipif(
    os.environ.get("CODEV_REINDEX_REAL_CGROUP") != "1",
    reason="需在 Delegate=yes 的临时 systemd unit 内显式运行",
)
def test_real_cgroup_parent_crash_before_activate_is_recoverable(
    tmp_path: Path,
) -> None:
    attempt_id = f"real-cgroup-parent-crash-{os.getpid()}"
    marker = tmp_path / "target-ran.txt"
    pid_path = tmp_path / "bootstrap.pid"
    repo_root = Path(__file__).parents[1].resolve()
    source = (
        "import os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from codev_platform.reindex.attempt_process import Deadline\n"
        "from codev_platform.reindex.cgroup_process import "
        "CgroupAttemptProcessBackend\n"
        "backend = CgroupAttemptProcessBackend(poll_interval=0.01)\n"
        "handle = backend.prepare(\n"
        f"    attempt_id={attempt_id!r},\n"
        f"    argv=[{sys.executable!r}, {str(support._FIXTURE.resolve())!r}, "
        f"'cgroup-probe', '--seconds', '1', '--state-path', {str(marker)!r}],\n"
        f"    cwd=Path({str(tmp_path)!r}),\n"
        f"    bootstrap_log=Path({str(tmp_path / 'parent-crash.log')!r}),\n"
        "    deadline=Deadline.start(3.0),\n"
        ")\n"
        f"Path({str(pid_path)!r}).write_text(str(handle.pid), encoding='ascii')\n"
        "os._exit(0)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=repo_root,
        check=False,
        timeout=6,
    )
    backend = CgroupAttemptProcessBackend(poll_interval=0.01)
    recovery = backend.recover(
        support._claimed_journal(attempt_id),
        Deadline.start(2.0),
    )

    assert completed.returncode == 0
    assert recovery.state is RecoveryState.NEVER_STARTED
    assert not marker.exists()
    assert not backend.filesystem.exists_attempt(attempt_id)


@pytest.mark.skipif(
    os.environ.get("CODEV_REINDEX_REAL_CGROUP") != "1",
    reason="需在 Delegate=yes 的临时 systemd unit 内显式运行",
)
def test_real_cgroup_kill_removes_setsid_grandchild(tmp_path: Path) -> None:
    backend = CgroupAttemptProcessBackend(poll_interval=0.01)
    pid_path = tmp_path / "escaped.pid"
    log_path = tmp_path / "bootstrap.log"
    handle = backend.prepare(
        attempt_id="real-cgroup-setsid",
        argv=[
            sys.executable,
            str(support._FIXTURE),
            "setsid-grandchild",
            "--seconds",
            "10",
            "--state-path",
            str(pid_path),
        ],
        cwd=tmp_path,
        bootstrap_log=log_path,
        deadline=Deadline.start(3.0),
    )
    assert not pid_path.exists()
    backend.activate(handle, Deadline.start(1.0))
    deadline = time.monotonic() + 3.0
    while not pid_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_path.exists()
    child_pid = support._wait_for_pid(pid_path)
    child_membership = Path(f"/proc/{child_pid}/cgroup").read_text(encoding="ascii")
    assert backend.filesystem.path_for(handle.native_ref).name in child_membership
    assert Path(f"/proc/{handle.pid}/fd/1").resolve() == log_path.resolve()
    assert Path(f"/proc/{handle.pid}/fd/2").resolve() == log_path.resolve()

    recovered = CgroupAttemptProcessBackend(poll_interval=0.01)
    recovery = recovered.recover(support._journal(handle), Deadline.start(1.0))
    assert recovery.state is RecoveryState.ACTIVE
    assert recovered.poll(handle) is None

    report = recovered.terminate(
        handle,
        grace_sec=0.05,
        deadline=Deadline.start(1.0),
    )

    assert report.forced is True
    assert report.confirmed_dead is True
    assert recovered.filesystem.exists(handle.native_ref) is False
    backend.poll(handle)
