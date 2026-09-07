"""systemctl 生产适配器的严格状态协议测试。"""

from __future__ import annotations

import subprocess

import pytest

import codev_platform.mcp_systemd_systemctl as systemctl_adapter
from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitProcessState,
    SystemdUnitState,
)


def _completed(stdout: str, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(("systemctl",), returncode, stdout=stdout, stderr="")


def test_进程状态严格解析三字段(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[str, ...]] = []
    invocation_id = "a" * 32

    def run(command: tuple[str, ...]):
        commands.append(command)
        return _completed(f"ActiveState=active\nMainPID=123\nInvocationID={invocation_id}\n")

    monkeypatch.setattr(systemctl_adapter, "run_systemctl_command", run)

    assert systemctl_adapter.read_unit_process_state("codev-web.service") == (
        SystemdUnitProcessState("active", 123, invocation_id)
    )
    assert commands == [
        (
            "systemctl",
            "show",
            "codev-web.service",
            "--property=ActiveState",
            "--property=MainPID",
            "--property=InvocationID",
        )
    ]


def test_timer进程状态允许systemd省略不存在的主进程(monkeypatch: pytest.MonkeyPatch) -> None:
    """timer 不是进程承载 unit，缺失 MainPID 必须规范为零而非放宽其他字段。"""
    invocation_id = "b" * 32
    monkeypatch.setattr(
        systemctl_adapter,
        "run_systemctl_command",
        lambda _command: _completed(f"ActiveState=active\nInvocationID={invocation_id}\n"),
    )

    assert systemctl_adapter.read_unit_process_state("codev-memory-maintenance.timer") == (
        SystemdUnitProcessState("active", 0, invocation_id)
    )


@pytest.mark.parametrize(
    "name,stdout,returncode",
    (
        ("codev-web.service", "ActiveState=active\nMainPID=1\n", 0),
        ("codev-web.service", "ActiveState=active\nMainPID=x\nInvocationID=abc\n", 0),
        (
            "codev-web.service",
            "ActiveState=active\nActiveState=active\nMainPID=1\nInvocationID=abc\n",
            0,
        ),
        ("codev-web.service", "ActiveState=active\nMainPID=1\nInvocationID=abc\nExtra=x\n", 0),
        (
            "codev-memory-maintenance.timer",
            "ActiveState=active\nInvocationID=abc\nExtra=x\n",
            0,
        ),
        ("codev-web.service", "private-output", 1),
    ),
)
def test_进程状态拒绝缺项重复非法值多项与命令失败(
    name: str,
    stdout: str,
    returncode: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        systemctl_adapter,
        "run_systemctl_command",
        lambda _command: _completed(stdout, returncode=returncode),
    )

    with pytest.raises(SystemdInstallTransactionError) as caught:
        systemctl_adapter.read_unit_process_state(name)

    assert "private-output" not in str(caught.value)


def test_生命周期恢复按原状态生成唯一命令(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(systemctl_adapter, "systemctl", commands.append)

    systemctl_adapter.restore_unit_file_state(
        "enabled.service",
        SystemdUnitState("enabled", "active"),
    )
    systemctl_adapter.restore_unit_file_state(
        "disabled.service",
        SystemdUnitState("disabled", "inactive"),
    )
    systemctl_adapter.restore_unit_activity_state(
        "active.service",
        SystemdUnitState("enabled", "active"),
    )
    systemctl_adapter.restore_unit_activity_state(
        "inactive.service",
        SystemdUnitState("disabled", "inactive"),
    )

    assert commands == [
        ("systemctl", "enable", "enabled.service"),
        ("systemctl", "disable", "disabled.service"),
        ("systemctl", "restart", "active.service"),
        ("systemctl", "stop", "inactive.service"),
    ]


def test_运行证明等待允许的过渡状态收敛(monkeypatch: pytest.MonkeyPatch) -> None:
    states = iter(("activating", "deactivating", "reloading", "active"))
    commands: list[tuple[str, ...]] = []
    sleeps: list[float] = []
    now = [0.0]
    monkeypatch.setattr(
        systemctl_adapter,
        "read_systemctl_state",
        lambda command: commands.append(command) or next(states),
    )
    monkeypatch.setattr(systemctl_adapter.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        systemctl_adapter.time,
        "sleep",
        lambda seconds: sleeps.append(seconds) or now.__setitem__(0, now[0] + seconds),
    )

    systemctl_adapter.verify_running_units(("codev-web.service",))

    assert commands == [("systemctl", "is-active", "codev-web.service")] * 4
    assert sleeps == [0.1, 0.1, 0.1]


def test_运行证明对终态立即失败且不等待(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(systemctl_adapter, "read_systemctl_state", lambda _command: "failed")
    monkeypatch.setattr(systemctl_adapter.time, "sleep", sleeps.append)

    with pytest.raises(SystemdInstallTransactionError, match="运行状态未证明"):
        systemctl_adapter.verify_running_units(("codev-web.service",))

    assert sleeps == []


def test_运行证明多个unit共享同一超时期限(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    now = [0.0]
    monkeypatch.setattr(systemctl_adapter, "read_systemctl_state", lambda _command: "activating")
    monkeypatch.setattr(systemctl_adapter.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        systemctl_adapter.time,
        "sleep",
        lambda seconds: (
            sleeps.append(seconds)
            or now.__setitem__(0, systemctl_adapter._RUNNING_STATE_READINESS_TIMEOUT_SEC)
        ),
    )

    with pytest.raises(SystemdInstallTransactionError, match="运行状态未证明"):
        systemctl_adapter.verify_running_units(("codev-web.service", "codev-agent.service"))

    assert sleeps == [0.1]


def test_systemctl子进程使用固定边界且不回显失败输出(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, object]] = []

    def run(*args, **kwargs):
        observed.append({"args": args, "kwargs": kwargs})
        return _completed("private-output", returncode=3)

    monkeypatch.setattr(systemctl_adapter.subprocess, "run", run)
    with pytest.raises(SystemdInstallTransactionError) as caught:
        systemctl_adapter.systemctl(("systemctl", "daemon-reload"))

    assert "private-output" not in str(caught.value)
    assert observed[0]["args"] == (("/usr/bin/systemctl", "daemon-reload"),)
    assert observed[0]["kwargs"] == {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 15.0,
        "check": False,
    }


@pytest.mark.parametrize(
    "command",
    ((), ("other", "status"), ("systemctl", ""), ("systemctl", "status\nnext")),
)
def test_systemctl拒绝非固定命令边界(command: tuple[str, ...]) -> None:
    with pytest.raises(SystemdInstallTransactionError, match="命令无效"):
        systemctl_adapter.run_systemctl_command(command)
