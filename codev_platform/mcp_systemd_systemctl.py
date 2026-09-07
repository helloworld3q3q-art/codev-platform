"""systemd 安装事务的进程状态读取与生命周期命令适配器。"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitProcessState,
    SystemdUnitState,
)


_SYSTEMCTL_TIMEOUT_SEC = 15.0
_SYSTEMCTL = "/usr/bin/systemctl"
_RUNNING_STATE_POLL_INTERVAL_SEC = 0.1
# 与受管 service 默认 TimeoutStartSec 对齐；成功即返回，非每个 unit 累加等待。
_RUNNING_STATE_READINESS_TIMEOUT_SEC = 90.0
_TRANSIENT_RUNNING_STATES = frozenset({"activating", "deactivating", "reloading"})
_PROCESS_STATE_BASE_PROPERTIES = frozenset({"ActiveState", "InvocationID"})
_PROCESS_STATE_MAIN_PID_PROPERTY = "MainPID"


def _trusted_systemctl_command(command: object) -> tuple[str, ...]:
    if (
        type(command) is not tuple
        or not command
        or command[0] != "systemctl"
        or not all(
            type(item) is str and item and "\x00" not in item and "\n" not in item
            for item in command
        )
    ):
        raise SystemdInstallTransactionError("systemd 安装命令无效")
    return (_SYSTEMCTL, *command[1:])


def read_systemctl_state(command: tuple[str, ...]) -> str:
    trusted = _trusted_systemctl_command(command)
    try:
        result = subprocess.run(
            trusted,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd unit 运行状态无法读取") from error
    value = result.stdout.strip()
    if not value or "\n" in value:
        raise SystemdInstallTransactionError("systemd unit 运行状态无法证明")
    return value


def run_systemctl_command(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    trusted = _trusted_systemctl_command(command)
    try:
        return subprocess.run(
            trusted,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SYSTEMCTL_TIMEOUT_SEC,
            check=False,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdInstallTransactionError("systemd 安装命令无法执行") from error


def read_unit_process_state(name: str) -> SystemdUnitProcessState:
    """读取 install-only 的精确进程身份，不以 active/inactive 粗粒度替代。"""
    result = run_systemctl_command(
        (
            "systemctl",
            "show",
            name,
            "--property=ActiveState",
            "--property=MainPID",
            "--property=InvocationID",
        )
    )
    if result.returncode != 0:
        raise SystemdInstallTransactionError("systemd unit 进程状态无法读取")
    return _parse_unit_process_state(name, result.stdout)


def _parse_unit_process_state(name: str, output: str) -> SystemdUnitProcessState:
    """规范化 systemd 的进程字段；timer 没有进程时以零 PID 表示。"""
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise SystemdInstallTransactionError("systemd unit 进程状态无法证明")
        values[key] = value
    timer = type(name) is str and name.endswith(".timer")
    expected = _PROCESS_STATE_BASE_PROPERTIES | (
        frozenset() if timer else frozenset({_PROCESS_STATE_MAIN_PID_PROPERTY})
    )
    allowed = expected | (
        frozenset({_PROCESS_STATE_MAIN_PID_PROPERTY}) if timer else frozenset()
    )
    if set(values) != expected and set(values) != allowed:
        raise SystemdInstallTransactionError("systemd unit 进程状态无法证明")
    if timer and _PROCESS_STATE_MAIN_PID_PROPERTY not in values:
        return SystemdUnitProcessState(values["ActiveState"], 0, values["InvocationID"])
    try:
        main_pid = int(values["MainPID"])
    except ValueError:
        raise SystemdInstallTransactionError("systemd unit 进程状态无法证明") from None
    return SystemdUnitProcessState(values["ActiveState"], main_pid, values["InvocationID"])


def restore_unit_file_state(name: str, state: SystemdUnitState) -> None:
    """恢复启用状态；调用方必须独立调用活动态恢复，禁止异常短路。"""
    action = "enable" if state.unit_file_state == "enabled" else "disable"
    systemctl(("systemctl", action, name))


def restore_unit_activity_state(name: str, state: SystemdUnitState) -> None:
    """恢复活动状态；即使启用态恢复失败也仍由事务单独尝试本动作。"""
    action = "restart" if state.active_state == "active" else "stop"
    systemctl(("systemctl", action, name))


def verify_running_units(names: tuple[str, ...]) -> None:
    """在共享有界期限内证明重启 unit 已收敛为 active。"""
    _wait_for_running_units(
        names,
        state_reader=read_systemctl_state,
        monotonic=time.monotonic,
        sleeper=time.sleep,
    )


def _wait_for_running_units(
    names: tuple[str, ...],
    *,
    state_reader: Callable[[tuple[str, ...]], str],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> None:
    """只等待 systemd 明确定义的过渡状态，终态和超时均失败关闭。"""
    deadline = monotonic() + _RUNNING_STATE_READINESS_TIMEOUT_SEC
    pending = names
    while pending:
        remaining: list[str] = []
        for name in pending:
            state = state_reader(("systemctl", "is-active", name))
            if type(state) is not str:
                raise SystemdInstallTransactionError("systemd unit 运行状态未证明")
            if state == "active":
                continue
            if state not in _TRANSIENT_RUNNING_STATES:
                raise SystemdInstallTransactionError("systemd unit 运行状态未证明")
            remaining.append(name)
        if not remaining:
            return
        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise SystemdInstallTransactionError("systemd unit 运行状态未证明")
        sleeper(min(_RUNNING_STATE_POLL_INTERVAL_SEC, remaining_seconds))
        pending = tuple(remaining)


def systemctl(command: tuple[str, ...]) -> None:
    result = run_systemctl_command(command)
    if result.returncode != 0:
        raise SystemdInstallTransactionError("systemd 安装命令失败")


__all__ = [
    "read_systemctl_state",
    "read_unit_process_state",
    "restore_unit_activity_state",
    "restore_unit_file_state",
    "run_systemctl_command",
    "systemctl",
    "verify_running_units",
]
