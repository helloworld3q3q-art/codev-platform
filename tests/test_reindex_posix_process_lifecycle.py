from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from codev_platform.reindex import posix_bootstrap, posix_process
from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
    ProcessBackendReadinessError,
)
from codev_platform.reindex.posix_process import (
    PosixAttemptProcessBackend,
)


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux /proc 进程组后端只在 Linux/WSL 验证",
)

from tests import reindex_posix_process_support as support


@pytest.mark.parametrize("poll_interval", [float("nan"), float("inf")])
def test_posix_rejects_nonfinite_poll_interval(poll_interval: float) -> None:
    with pytest.raises(ValueError, match="poll_interval"):
        PosixAttemptProcessBackend(poll_interval=poll_interval)


def test_posix_rejects_noncallable_runtime_dependency() -> None:
    with pytest.raises(ValueError, match="必须可调用"):
        PosixAttemptProcessBackend(sleeper=None)


def test_posix_backend_explicitly_rejects_production_readiness() -> None:
    backend = PosixAttemptProcessBackend()

    with pytest.raises(ProcessBackendReadinessError, match="POSIX|生产"):
        backend.assert_ready(Deadline.start(0.1))


def test_posix_invalid_attempt_id_is_rejected_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        posix_process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得创建进程")),
    )
    backend = PosixAttemptProcessBackend()

    with pytest.raises(ValueError, match="attempt_id"):
        backend.prepare(
            attempt_id=" ",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "invalid-attempt.log",
            deadline=Deadline.start(1.0),
        )


def test_posix_invalid_wall_clock_is_rejected_before_spawn(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        posix_process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不得创建进程")),
    )
    backend = PosixAttemptProcessBackend(wall_clock=lambda: float("nan"))

    with pytest.raises(ValueError, match="时钟"):
        backend.prepare(
            attempt_id="invalid-clock",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "invalid-clock.log",
            deadline=Deadline.start(1.0),
        )


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (ValueError("spawn failed"), AttemptProcessStartError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_posix_spawn_failure_closes_gate_for_every_exception(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected_error: type[BaseException],
) -> None:
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(posix_process.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        posix_process.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    backend = PosixAttemptProcessBackend()

    try:
        with pytest.raises(expected_error):
            backend.prepare(
                attempt_id="spawn-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "spawn-failure.log",
                deadline=Deadline.start(1.0),
            )
        for descriptor in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (RuntimeError("builder failed"), AttemptProcessStartError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_posix_pre_spawn_resource_failure_closes_both_gate_descriptors(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected_error: type[BaseException],
) -> None:
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(posix_process.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        posix_process,
        "build_bootstrap_command",
        lambda *_args: (_ for _ in ()).throw(failure),
    )
    backend = PosixAttemptProcessBackend()

    try:
        with pytest.raises(expected_error):
            backend.prepare(
                attempt_id="resource-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "resource-failure.log",
                deadline=Deadline.start(1.0),
            )
        for descriptor in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_posix_registration_failure_reaps_blocked_bootstrap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    spawned: list[subprocess.Popen] = []
    real_popen = posix_process.subprocess.Popen

    def _spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    monkeypatch.setattr(posix_process.subprocess, "Popen", _spawn)
    monkeypatch.setattr(
        backend._table,
        "build_identity",
        lambda *_args: (_ for _ in ()).throw(ValueError("identity failed")),
    )

    try:
        with pytest.raises(AttemptProcessStartError):
            backend.prepare(
                attempt_id="registration-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "registration-failure.log",
                deadline=Deadline.start(2.0),
            )
        assert len(spawned) == 1
        spawned[0].wait(timeout=1.0)
        assert spawned[0].poll() is not None
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)


def test_posix_post_spawn_wait_failure_is_transactionally_cleaned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    spawned: list[subprocess.Popen] = []
    real_popen = posix_process.subprocess.Popen

    def _spawn(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def _fail_sleep(_seconds: float) -> None:
        raise RuntimeError("sleep failed")

    backend = PosixAttemptProcessBackend(
        poll_interval=0.01,
        sleeper=_fail_sleep,
    )
    monkeypatch.setattr(posix_process.subprocess, "Popen", _spawn)
    monkeypatch.setattr(backend._table, "read", lambda _pid: None)

    try:
        with pytest.raises(AttemptProcessStartError):
            backend.prepare(
                attempt_id="wait-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "wait-failure.log",
                deadline=Deadline.start(2.0),
            )
        assert len(spawned) == 1
        spawned[0].wait(timeout=1.0)
        assert spawned[0].poll() is not None
    finally:
        for process in spawned:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)


def test_posix_prepare_blocks_target_until_activate(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran.txt"
    backend = PosixAttemptProcessBackend(poll_interval=0.01)

    handle = backend.prepare(
        attempt_id="blocked-before-activate",
        argv=[
            sys.executable,
            str(support._FIXTURE),
            "cgroup-probe",
            "--seconds",
            "10",
            "--state-path",
            str(marker),
        ],
        cwd=tmp_path,
        bootstrap_log=tmp_path / "blocked.log",
        deadline=Deadline.start(2.0),
    )
    time.sleep(0.15)

    assert not marker.exists()

    backend.activate(handle, Deadline.start(1.0))
    assert backend._live[handle.process_identity].uncertain is True
    support._wait_for_file(marker)
    backend.terminate(handle, grace_sec=0.0, deadline=Deadline.start(1.0))


def test_posix_blocked_prepare_can_prove_target_never_activated(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "target-ran.txt"
    backend = PosixAttemptProcessBackend(poll_interval=0.01)
    handle = backend.prepare(
        attempt_id="blocked-proof",
        argv=[
            sys.executable,
            str(support._FIXTURE),
            "cgroup-probe",
            "--seconds",
            "10",
            "--state-path",
            str(marker),
        ],
        cwd=tmp_path,
        bootstrap_log=tmp_path / "blocked-proof.log",
        deadline=Deadline.start(2.0),
    )

    report = backend.terminate(
        handle,
        grace_sec=0.05,
        deadline=Deadline.start(1.0),
    )

    assert report.confirmed_dead is True
    assert report.death_proof is not None
    assert "未放行" in report.death_proof.evidence
    assert not marker.exists()


def test_posix_blocked_proofs_clamp_epoch_when_wall_clock_moves_backward(
    tmp_path: Path,
) -> None:
    wall = [1_800_000_000.0]
    backend = PosixAttemptProcessBackend(
        poll_interval=0.01,
        wall_clock=lambda: wall[0],
    )
    first = backend.prepare(
        attempt_id="wall-rollback-terminate",
        argv=[sys.executable, str(support._FIXTURE), "success"],
        cwd=tmp_path,
        bootstrap_log=tmp_path / "wall-rollback-terminate.log",
        deadline=Deadline.start(2.0),
    )
    wall[0] = 1_000_000_000.0

    termination = backend.terminate(
        first,
        grace_sec=0.05,
        deadline=Deadline.start(1.0),
    )

    wall[0] = 1_800_000_000.0
    second = backend.prepare(
        attempt_id="wall-rollback-confirm",
        argv=[sys.executable, str(support._FIXTURE), "success"],
        cwd=tmp_path,
        bootstrap_log=tmp_path / "wall-rollback-confirm.log",
        deadline=Deadline.start(2.0),
    )
    wall[0] = 1_000_000_000.0
    backend._close_activation(backend._live[second.process_identity])
    confirmed = backend.confirm_dead(second, Deadline.start(1.0))

    assert termination.death_proof is not None
    assert termination.requested_at == first.started_at
    assert termination.finished_at == first.started_at
    assert termination.death_proof.confirmed_at == first.started_at
    assert confirmed is not None
    assert confirmed.confirmed_at == second.started_at


def test_posix_parent_crash_before_activate_never_executes_target(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "target-ran.txt"
    pid_path = tmp_path / "bootstrap.pid"
    repo_root = Path(__file__).parents[1].resolve()
    source = (
        "import os, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from codev_platform.reindex.attempt_process import Deadline\n"
        "from codev_platform.reindex.posix_process import "
        "PosixAttemptProcessBackend\n"
        "backend = PosixAttemptProcessBackend(poll_interval=0.01)\n"
        "handle = backend.prepare(\n"
        "    attempt_id='parent-crash',\n"
        f"    argv=[{sys.executable!r}, {str(support._FIXTURE.resolve())!r}, "
        f"'cgroup-probe', '--seconds', '1', '--state-path', {str(marker)!r}],\n"
        f"    cwd=Path({str(tmp_path)!r}),\n"
        f"    bootstrap_log=Path({str(tmp_path / 'parent-crash.log')!r}),\n"
        "    deadline=Deadline.start(2.0),\n"
        ")\n"
        f"Path({str(pid_path)!r}).write_text(str(handle.pid), encoding='ascii')\n"
        "os._exit(0)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=repo_root,
        check=False,
        timeout=5,
    )
    support._wait_for_file(pid_path)
    bootstrap_pid = support._wait_for_pid(pid_path)
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{bootstrap_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert completed.returncode == 0
    assert not marker.exists()
    assert not Path(f"/proc/{bootstrap_pid}").exists()


def test_posix_parent_guard_rejects_changed_parent(monkeypatch) -> None:
    monkeypatch.setattr(posix_bootstrap, "_set_parent_death_signal", lambda: True)
    monkeypatch.setattr(posix_bootstrap.os, "getppid", lambda: 12)

    assert posix_bootstrap._arm_parent_guard(11) is False


def test_posix_identity_wait_never_sleeps_with_negative_duration() -> None:
    moments = iter((0.0, 1.0, 1.1))
    sleeps: list[float] = []
    backend = PosixAttemptProcessBackend(
        monotonic=lambda: next(moments),
        sleeper=sleeps.append,
    )

    assert backend._read_started_process(99_999_999, Deadline(0.5)) is None
    assert sleeps == []
