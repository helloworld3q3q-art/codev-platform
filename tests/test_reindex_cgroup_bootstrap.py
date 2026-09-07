from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.reindex.attempt_process import (
    AttemptProcessStartError,
    Deadline,
)
from codev_platform.reindex import (
    bootstrap_runtime,
    cgroup_abort,
    cgroup_bootstrap,
    cgroup_spawn,
    posix_bootstrap,
)
from codev_platform.reindex.cgroup_process import (
    CgroupAttemptProcessBackend,
    encode_cgroup_native_ref,
)

from tests import reindex_cgroup_process_support as support

def test_cgroup_backend_uses_absolute_bootstrap_script(tmp_path: Path) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    native_ref = fs.create_attempt("absolute-bootstrap")

    command = cgroup_bootstrap.build_bootstrap_command(
        [sys.executable, "-c", "pass"],
        fs.path_for(native_ref),
        9,
        123,
    )

    assert command[1:3] == ["-I", "-S"]
    assert Path(command[3]).is_absolute()
    assert command[3].endswith("cgroup_bootstrap.py")
    assert "-m" not in command[:4]


def test_posix_bootstrap_flags_are_isolated_from_target_argv() -> None:
    target = [sys.executable, "-c", "print('-I -S')"]

    command = posix_bootstrap.build_bootstrap_command(target, 9, 123)

    assert command[0] == sys.executable
    assert command[1:3] == ["-I", "-S"]
    assert Path(command[3]).is_absolute()
    separator = command.index("--")
    assert command[separator + 1 :] == target


def test_bootstrap_isolated_mode_never_loads_sitecustomize(tmp_path: Path) -> None:
    import_root = tmp_path / "python-imports"
    import_root.mkdir()
    marker = tmp_path / "site-loaded.txt"
    (import_root / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded', encoding='ascii')\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(import_root)
    control = subprocess.run(
        [sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env=env,
        check=False,
        timeout=3,
    )
    assert control.returncode == 0
    assert marker.exists()
    marker.unlink()
    target = [sys.executable, "-c", "pass"]
    commands = (
        posix_bootstrap.build_bootstrap_command(target, 9, 0),
        cgroup_bootstrap.build_bootstrap_command(
            target,
            tmp_path / ("attempt-" + "a" * 64),
            9,
            0,
        ),
    )

    for command in commands:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            env=env,
            check=False,
            timeout=3,
        )
        assert completed.returncode == 125
    assert not marker.exists()


def test_cgroup_bootstrap_uses_python_not_target_executable(tmp_path: Path) -> None:
    fs = support._FakeCgroupFs(tmp_path)
    native_ref = fs.create_attempt("non-python-target")
    target = str((tmp_path / "native-tool").resolve())

    command = cgroup_bootstrap.build_bootstrap_command(
        [target, "--version"],
        fs.path_for(native_ref),
        9,
        123,
    )

    assert command[0] == sys.executable
    assert command[1:3] == ["-I", "-S"]
    separator = command.index("--")
    assert command[separator + 1 :] == [target, "--version"]


def test_cgroup_bootstrap_accepts_minimal_absolute_target(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cgroup_bootstrap.py",
            "--cgroup",
            "/sys/fs/cgroup/attempt-" + "a" * 64,
            "--ready-fd",
            "9",
            "--expected-parent-pid",
            str(os.getpid()),
            "--",
            sys.executable,
        ],
    )

    parsed = cgroup_bootstrap._arguments()

    assert parsed is not None
    assert parsed[1] == 9
    assert parsed[2] == os.getpid()
    assert parsed[3] == [sys.executable]


def test_cgroup_parent_guard_rejects_changed_parent(monkeypatch) -> None:
    monkeypatch.setattr(cgroup_bootstrap, "_set_parent_death_signal", lambda: True)
    monkeypatch.setattr(cgroup_bootstrap.os, "getppid", lambda: 12)

    assert cgroup_bootstrap._arm_parent_guard(11) is False


def test_cgroup_log_open_failure_removes_empty_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)

    def _fail_log(_path: Path):
        raise OSError("disk full")

    monkeypatch.setattr(cgroup_spawn, "open_direct_log", _fail_log)

    with pytest.raises(AttemptProcessStartError):
        backend.prepare(
            attempt_id="log-failure",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "bootstrap.log",
            deadline=Deadline(clock.monotonic() + 1.0),
        )

    assert fs.removed == 1
    assert fs.present is False


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (ValueError("spawn failed"), AttemptProcessStartError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_cgroup_spawn_failure_closes_gate_and_removes_group(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected_error: type[BaseException],
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(cgroup_spawn.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        cgroup_spawn.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )

    try:
        with pytest.raises(expected_error):
            backend.prepare(
                attempt_id="spawn-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "spawn-failure.log",
                deadline=Deadline(clock.monotonic() + 1.0),
            )
        for descriptor in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert fs.removed == 1
        assert fs.present is False
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
def test_cgroup_pre_spawn_resource_failure_is_transactionally_cleaned(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected_error: type[BaseException],
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(cgroup_spawn.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        cgroup_spawn,
        "build_bootstrap_command",
        lambda *_args: (_ for _ in ()).throw(failure),
    )

    try:
        with pytest.raises(expected_error):
            backend.prepare(
                attempt_id="resource-failure",
                argv=[sys.executable, "-c", "pass"],
                cwd=tmp_path,
                bootstrap_log=tmp_path / "resource-failure.log",
                deadline=Deadline(clock.monotonic() + 1.0),
            )
        for descriptor in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(descriptor)
        assert fs.removed == 1
        assert fs.present is False
    finally:
        for descriptor in (read_fd, write_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def test_cgroup_environment_preflight_failure_cleans_before_opening_pipe(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)
    native_ref = encode_cgroup_native_ref(support._BOOT_ID, "preflight-failure")

    def _create(_attempt_id: str) -> str:
        fs.present = True
        return native_ref

    monkeypatch.setattr(fs, "create_attempt", _create)
    monkeypatch.setattr(
        fs,
        "path_for",
        lambda _native_ref: (_ for _ in ()).throw(RuntimeError("path failed")),
    )
    monkeypatch.setattr(
        cgroup_spawn.os,
        "pipe",
        lambda: (_ for _ in ()).throw(AssertionError("不得打开 pipe")),
    )

    with pytest.raises(AttemptProcessStartError):
        backend.prepare(
            attempt_id="preflight-failure",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "preflight-failure.log",
            deadline=Deadline(clock.monotonic() + 1.0),
        )

    assert fs.removed == 1
    assert fs.present is False


def test_cgroup_registration_failure_closes_gate_and_cleans_group(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    backend = support._backend(fs, table, clock)
    read_fd, write_fd = os.pipe()
    kills: list[int] = []

    class _BlockedProcess:
        pid = 321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def kill():
            kills.append(1)

        @staticmethod
        def wait(*, timeout):
            del timeout
            return 125

    monkeypatch.setattr(cgroup_spawn.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        cgroup_spawn.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _BlockedProcess(),
    )
    monkeypatch.setattr(
        table,
        "build_identity",
        lambda *_args: (_ for _ in ()).throw(ValueError("identity failed")),
    )

    with pytest.raises(AttemptProcessStartError):
        backend.prepare(
            attempt_id="registration-failure",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "bootstrap.log",
            deadline=Deadline(clock.monotonic() + 1.0),
        )

    with pytest.raises(OSError):
        os.fstat(write_fd)
    assert fs.kills == 1
    assert fs.removed == 1
    assert kills == [1]


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (RuntimeError("sleep failed"), AttemptProcessStartError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_cgroup_post_spawn_wait_failure_is_transactionally_cleaned(
    tmp_path: Path,
    monkeypatch,
    failure: BaseException,
    expected_error: type[BaseException],
) -> None:
    clock = support._FakeClock()
    fs = support._FakeCgroupFs(tmp_path)
    table = support._FakeProcessTable()
    read_fd, write_fd = os.pipe()
    kills: list[int] = []

    class _BlockedProcess:
        pid = 321

        @staticmethod
        def poll():
            return None

        @staticmethod
        def kill():
            kills.append(1)

        @staticmethod
        def wait(*, timeout):
            del timeout
            return 125

    def _fail_sleep(_seconds: float) -> None:
        raise failure

    backend = CgroupAttemptProcessBackend(
        filesystem=fs,
        process_table=table,
        monotonic=clock.monotonic,
        wall_clock=clock.wall,
        sleeper=_fail_sleep,
    )
    monkeypatch.setattr(fs, "contains_pid", lambda *_args: False)
    monkeypatch.setattr(cgroup_spawn.os, "pipe", lambda: (read_fd, write_fd))
    monkeypatch.setattr(
        cgroup_spawn.subprocess,
        "Popen",
        lambda *_args, **_kwargs: _BlockedProcess(),
    )

    with pytest.raises(expected_error):
        backend.prepare(
            attempt_id="wait-failure",
            argv=[sys.executable, "-c", "pass"],
            cwd=tmp_path,
            bootstrap_log=tmp_path / "wait-failure.log",
            deadline=Deadline(clock.monotonic() + 1.0),
        )

    with pytest.raises(OSError):
        os.fstat(write_fd)
    assert fs.kills == 1
    assert fs.removed == 1
    assert kills == [1]


def test_cgroup_abort_cleanup_uses_one_shared_deadline(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = support._FakeClock()
    clock.monotonic_value = 0.0
    fs = support._StubbornCgroupFs(tmp_path)
    fs.present = False
    backend = support._backend(fs, support._FakeProcessTable(), clock)
    waits: list[float] = []
    killed_at: list[float] = []

    class _BlockedProcess:
        pid = 999

        @staticmethod
        def kill():
            killed_at.append(clock.monotonic())

        @staticmethod
        def wait(*, timeout):
            waits.append(timeout)
            clock.sleep(timeout)
            raise subprocess.TimeoutExpired("bootstrap", timeout)

    monkeypatch.setattr(cgroup_abort.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bootstrap_runtime.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(bootstrap_runtime.time, "sleep", clock.sleep)

    backend._preparer.abort(
        _BlockedProcess(),
        encode_cgroup_native_ref(support._BOOT_ID, "cleanup-deadline"),
        Deadline(0.25),
    )

    assert len(waits) == 1
    assert waits[0] == pytest.approx(0.0, abs=1e-9)
    assert killed_at == [0.0]
    assert clock.monotonic() == pytest.approx(0.25)
