"""reindex 维护窗口的 systemd 命令、状态解析与稳定窗口叶子。"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable


_REINDEX_UNIT = "codev-reindex.service"
_SYSTEMCTL_TIMEOUT_SEC = 10.0

CommandRunner = Callable[..., object]
StabilityProbe = Callable[[], None]


class ReindexMaintenanceError(RuntimeError):
    """reindex 专用维护窗口无法安全准备或恢复。"""


def _run_systemctl(
    run: CommandRunner,
    command: tuple[str, ...],
    *,
    timeout_sec: float = _SYSTEMCTL_TIMEOUT_SEC,
) -> object:
    try:
        result = run(command, timeout_sec=timeout_sec)
    except MemoryError:
        raise
    except Exception as error:
        raise ReindexMaintenanceError("systemctl 维护命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise ReindexMaintenanceError("systemctl 维护命令失败")
    return result


def _read_systemctl_properties(
    run: CommandRunner,
    *,
    unit: str,
    properties: tuple[str, ...],
    error_message: str,
    timeout_sec: float = _SYSTEMCTL_TIMEOUT_SEC,
) -> dict[str, str]:
    """按字段名解析 systemctl show，避免不同 systemd 版本的输出顺序差异。"""
    result = _run_systemctl(
        run,
        (
            "systemctl",
            "show",
            unit,
            *(f"--property={name}" for name in properties),
        ),
        timeout_sec=timeout_sec,
    )
    output = getattr(result, "stdout", None)
    if type(output) is not str:
        raise ReindexMaintenanceError(error_message)
    values: dict[str, str] = {}
    expected = set(properties)
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator != "=" or key not in expected or key in values:
            raise ReindexMaintenanceError(error_message)
        values[key] = value
    if set(values) != expected:
        raise ReindexMaintenanceError(error_message)
    return values


def _require_baseline_restart(run: CommandRunner) -> None:
    result = _run_systemctl(
        run,
        (
            "systemctl",
            "show",
            _REINDEX_UNIT,
            "--property=Restart",
            "--value",
        ),
    )
    output = getattr(result, "stdout", None)
    if type(output) is not str or output.splitlines() != ["always"]:
        raise ReindexMaintenanceError("reindex 基线自动重启策略未恢复")


def _default_stability_probe(
    run: CommandRunner,
    *,
    expected_restarts: int,
) -> StabilityProbe:
    def probe() -> None:
        _wait_for_stable_worker(run, expected_restarts=expected_restarts)

    return probe


def _wait_for_stable_worker(
    run: CommandRunner,
    *,
    expected_restarts: int,
    startup_timeout_sec: float = 20.0,
    stable_window_sec: float = 5.0,
    interval_sec: float = 0.5,
) -> None:
    """有界确认 unit 已运行且自动重启计数在稳定窗口内不增长。"""
    deadline = time.monotonic() + startup_timeout_sec
    stable_until: float | None = None
    while time.monotonic() <= deadline:
        active, sub, restarts = _read_running_state(run)
        if restarts != expected_restarts:
            raise ReindexMaintenanceError("reindex worker 启动后发生自动重启")
        if active == "active" and sub == "running":
            if stable_until is None:
                stable_until = time.monotonic() + stable_window_sec
            if stable_until is not None and time.monotonic() >= stable_until:
                return
        elif stable_until is not None:
            raise ReindexMaintenanceError("reindex worker 稳定窗口内退出")
        time.sleep(interval_sec)
    raise ReindexMaintenanceError("reindex worker 未在限定时间内稳定运行")


def _read_running_state(run: CommandRunner) -> tuple[str, str, int]:
    values = _read_systemctl_properties(
        run,
        unit=_REINDEX_UNIT,
        properties=("ActiveState", "SubState", "NRestarts"),
        error_message="reindex 运行状态格式无效",
    )
    restarts = values["NRestarts"]
    if not restarts.isascii() or not restarts.isdigit():
        raise ReindexMaintenanceError("reindex 运行状态格式无效")
    return values["ActiveState"], values["SubState"], int(restarts)


def _read_restart_count(run: CommandRunner) -> int:
    result = _run_systemctl(
        run,
        (
            "systemctl",
            "show",
            _REINDEX_UNIT,
            "--property=NRestarts",
            "--value",
        ),
    )
    output = getattr(result, "stdout", None)
    values = output.splitlines() if type(output) is str else []
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise ReindexMaintenanceError("reindex 重启计数格式无效")
    return int(values[0])


def _stop_reindex_safely(run: CommandRunner) -> None:
    """恢复失败后只强制收敛目标 cgroup，失败仍由补偿结果如实记录。"""
    try:
        _run_systemctl(run, ("systemctl", "stop", _REINDEX_UNIT))
    except ReindexMaintenanceError:
        _run_systemctl(
            run,
            ("systemctl", "kill", "--kill-who=all", "--signal=SIGKILL", _REINDEX_UNIT),
        )
    finally:
        _run_systemctl(run, ("systemctl", "reset-failed", _REINDEX_UNIT))


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


__all__ = [
    "CommandRunner",
    "ReindexMaintenanceError",
    "StabilityProbe",
    "_REINDEX_UNIT",
    "_SYSTEMCTL_TIMEOUT_SEC",
    "_default_command_runner",
    "_default_stability_probe",
    "_read_restart_count",
    "_read_systemctl_properties",
    "_require_baseline_restart",
    "_run_systemctl",
    "_stop_reindex_safely",
    "_wait_for_stable_worker",
    "time",
]
