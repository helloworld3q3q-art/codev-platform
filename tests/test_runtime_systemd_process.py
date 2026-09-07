"""systemd 受管进程适配器的命令与互斥契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
import threading
import time

import pytest

from codev_platform.runtime_managed_process import (
    ManagedProcessLimits,
    ManagedProcessSpec,
)
import codev_platform.runtime_systemd_process as systemd_process


def _spec() -> ManagedProcessSpec:
    return ManagedProcessSpec(
        unit_slot="candidate-build",
        argv=("/usr/bin/setpriv", "--reuid=1001", "/usr/bin/env", "-i", "/bin/true"),
        working_directory="/",
        environment=(),
        environment_file=None,
        user=None,
        limits=ManagedProcessLimits(
            runtime_sec=900.0,
            stop_sec=30.0,
            stdout_limit_bytes=64 * 1024,
            stderr_limit_bytes=64 * 1024,
            tasks_max=128,
            memory_high_bytes=6 * 1024**3,
            memory_max_bytes=8 * 1024**3,
            memory_swap_max_bytes=0,
            cpu_quota_percent=400,
        ),
    )


def test_systemd命令固定完整资源属性且直接环境为空() -> None:
    command = systemd_process.build_systemd_run_command(
        _spec(),
        "codev-runtime-candidate-build.service",
    )
    properties = {item for item in command if item.startswith("--property=")}

    assert {
        "--property=ExitType=cgroup",
        "--property=RuntimeMaxSec=900s",
        "--property=TimeoutStopSec=30s",
        "--property=KillMode=control-group",
        "--property=SendSIGKILL=yes",
        "--property=OOMPolicy=kill",
        "--property=TasksMax=128",
        f"--property=MemoryHigh={6 * 1024**3}",
        f"--property=MemoryMax={8 * 1024**3}",
        "--property=MemorySwapMax=0",
        "--property=CPUQuota=400%",
        "--property=UMask=0077",
        "--property=NoNewPrivileges=yes",
        "--property=CollectMode=inactive-or-failed",
    } <= properties
    assert command.count("--wait") == 1
    assert command.count("--pipe") == 1
    assert command.count("--collect") == 1
    assert command.count("--expand-environment=no") == 1
    assert not any(item.startswith("--setenv=") for item in command)


def test_spawn使用固定客户端环境且冻结三个可信程序(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    verified: list[Path] = []
    process = object()
    monkeypatch.setattr(
        systemd_process,
        "verify_root_controlled_executable",
        lambda path: verified.append(path) or path,
    )
    monkeypatch.setattr(
        systemd_process,
        "popen_tree",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or process,
    )

    result = systemd_process.spawn_systemd_unit(
        _spec(),
        "codev-runtime-candidate-build.service",
    )

    assert result is process
    assert verified == [
        Path("/usr/bin/systemd-run"),
        Path("/usr/bin/systemctl"),
        Path("/usr/bin/python3"),
    ]
    assert captured["kwargs"]["env"] == {
        "PATH": systemd_process.os.defpath,
        "LC_ALL": "C.UTF-8",
    }


def test_EnvironmentFile在启动点复验并按键白名单重建payload环境(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    environment_file = Path("/etc/codev-platform/platform.env")
    spec = _spec()
    spec = type(spec)(
        unit_slot=spec.unit_slot,
        argv=spec.argv,
        working_directory=spec.working_directory,
        environment=(),
        environment_file=environment_file,
        user=spec.user,
        limits=spec.limits,
    )
    validated: list[Path] = []
    monkeypatch.setattr(
        systemd_process,
        "validate_systemd_environment_file",
        lambda path: validated.append(path) or frozenset({"DATABASE_URL", "PLATFORM_TOKEN"}),
    )
    monkeypatch.setattr(systemd_process, "verify_root_controlled_executable", lambda path: path)
    monkeypatch.setattr(
        systemd_process,
        "popen_tree",
        lambda command, **kwargs: captured.update(command=command, kwargs=kwargs) or object(),
    )

    systemd_process.spawn_systemd_unit(
        spec,
        "codev-runtime-candidate-build.service",
    )

    command = captured["command"]
    script_index = command.index(systemd_process._EXEC_EXACT_ENVIRONMENT)
    keys = json.loads(command[script_index + 1])
    assert validated == [environment_file]
    assert keys == ["DATABASE_URL", "PLATFORM_TOKEN"]
    assert captured["kwargs"]["env"] == {
        "PATH": systemd_process.os.defpath,
        "LC_ALL": "C.UTF-8",
    }


def test_EnvironmentFile通配路径在读取文件前失败关闭(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _spec()
    object.__setattr__(spec, "environment_file", Path("/etc/codev-platform/*.env"))
    monkeypatch.setattr(
        systemd_process,
        "validate_systemd_environment_file",
        lambda _path: pytest.fail("通配路径不得进入文件读取验证"),
    )

    with pytest.raises(systemd_process.RuntimeSystemdProcessError):
        systemd_process.build_systemd_run_command(
            spec,
            "codev-runtime-candidate-build.service",
        )


def test_abort客户端后仍存活必须失败关闭(monkeypatch: pytest.MonkeyPatch) -> None:
    process = type(
        "Process",
        (),
        {
            "stdout": None,
            "stderr": None,
            "poll": lambda self: None,
            "wait": lambda self, timeout: (_ for _ in ()).throw(
                systemd_process.subprocess.TimeoutExpired("systemd-run", timeout)
            ),
        },
    )()
    monkeypatch.setattr(systemd_process, "kill_process_tree", lambda *_args, **_kwargs: None)

    with pytest.raises(systemd_process.RuntimeSystemdProcessError):
        systemd_process.abort_systemd_run_client(process, 0.1)


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="需要 POSIX flock")
def test_同slot互斥而不同slot可并行(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(systemd_process, "_LOCK_ROOT", tmp_path / "locks")
    entered = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with systemd_process.acquire_systemd_unit_lock("codev-runtime-a.service", 1.0):
            entered.set()
            release.wait(2.0)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(1.0)
    with systemd_process.acquire_systemd_unit_lock("codev-runtime-b.service", 0.1):
        pass
    started = time.monotonic()
    with pytest.raises(systemd_process.RuntimeSystemdProcessError):
        with systemd_process.acquire_systemd_unit_lock("codev-runtime-a.service", 0.05):
            pass
    assert time.monotonic() - started < 0.5
    release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
