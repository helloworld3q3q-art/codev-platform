"""WSL systemd 维护操作前的受管单元停机证明。"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from codev_platform.core.cgroup_events import parse_cgroup_events

_REINDEX_UNIT = "codev-reindex.service"
_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_CGROUP_ROOT = Path("/sys/fs/cgroup")
_CONTROL_GROUP_PART = re.compile(r"[A-Za-z0-9_.@:-]+\Z")
_SERVICE_UNIT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*\.service\Z")
_RESTART_VALUE = re.compile(r"[a-z][a-z-]*\Z")
_SYSTEMCTL_TIMEOUT_SEC = 5.0


class LegacyWorkerStopProofError(RuntimeError):
    """无法证明旧 systemd reindex worker 及其后代均已停止。"""


CommandRunner = Callable[..., object]
CgroupEventsReader = Callable[[Path], bytes]


def verify_codev_reindex_stopped(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    cgroup_events_reader: CgroupEventsReader | None = None,
    cgroup_root: Path = _CGROUP_ROOT,
) -> None:
    """保持原有 reindex 停机证明接口。"""
    verify_systemd_unit_stopped(
        _REINDEX_UNIT,
        expected_restart="no",
        platform_name=platform_name,
        command_runner=command_runner,
        cgroup_events_reader=cgroup_events_reader,
        cgroup_root=cgroup_root,
    )


def verify_codev_codegraph_stopped(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    cgroup_events_reader: CgroupEventsReader | None = None,
    cgroup_root: Path = _CGROUP_ROOT,
) -> None:
    """证明 CodeGraph 单元已停机且保留正常的 ``Restart=always`` 基线。

    维护期的启动禁令由 runtime mask 承担；不能把 ``Restart=no`` 误当作
    CodeGraph 的长期 hold，否则显式恢复后会悄然偏离常态单元策略。
    """
    verify_systemd_unit_stopped(
        _CODEGRAPH_UNIT,
        expected_restart="always",
        platform_name=platform_name,
        command_runner=command_runner,
        cgroup_events_reader=cgroup_events_reader,
        cgroup_root=cgroup_root,
    )


def verify_codev_codegraph_runtime_masked_stopped(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    cgroup_events_reader: CgroupEventsReader | None = None,
    cgroup_root: Path = _CGROUP_ROOT,
) -> None:
    """证明 runtime mask 生效后的 CodeGraph 单元已停机。"""
    verify_systemd_unit_stopped(
        _CODEGRAPH_UNIT,
        expected_restart="no",
        platform_name=platform_name,
        command_runner=command_runner,
        cgroup_events_reader=cgroup_events_reader,
        cgroup_root=cgroup_root,
    )


def verify_codev_codegraph_running(
    *,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    cgroup_events_reader: CgroupEventsReader | None = None,
    cgroup_root: Path = _CGROUP_ROOT,
) -> None:
    """证明固定 CodeGraph unit 已运行、保持基线策略且拥有活动 cgroup。"""
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise LegacyWorkerStopProofError("当前平台无法证明 CodeGraph 受管服务已运行")
    run = _default_command_runner if command_runner is None else command_runner
    read_events = _read_cgroup_events if cgroup_events_reader is None else cgroup_events_reader
    if not callable(run) or not callable(read_events):
        raise LegacyWorkerStopProofError("CodeGraph 运行证明适配器不可用")
    active, sub, control_group, restart = _read_unit_state(run, _CODEGRAPH_UNIT)
    expected_group = f"/system.slice/{_CODEGRAPH_UNIT}"
    if active != "active" or sub != "running":
        raise LegacyWorkerStopProofError("CodeGraph 受管服务未处于运行态")
    if restart != "always":
        raise LegacyWorkerStopProofError("CodeGraph 受管服务自动重启策略不符合基线")
    if control_group != expected_group:
        raise LegacyWorkerStopProofError("CodeGraph 受管服务 ControlGroup 无法证明")
    events_path = _cgroup_events_path(cgroup_root, control_group, _CODEGRAPH_UNIT)
    try:
        populated = parse_cgroup_events(read_events(events_path))
    except MemoryError:
        raise
    except Exception as error:
        raise LegacyWorkerStopProofError("无法确认 CodeGraph 受管服务 cgroup") from error
    if not populated:
        raise LegacyWorkerStopProofError("CodeGraph 受管服务 cgroup 未填充")


def verify_systemd_unit_stopped(
    unit: str,
    *,
    expected_restart: str,
    platform_name: str | None = None,
    command_runner: CommandRunner | None = None,
    cgroup_events_reader: CgroupEventsReader | None = None,
    cgroup_root: Path = _CGROUP_ROOT,
) -> None:
    """要求指定受管 unit 已死、重启策略匹配且整个 cgroup 没有后代进程。"""
    service_unit = _validate_service_unit(unit)
    restart_expected = _validate_expected_restart(expected_restart)
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise LegacyWorkerStopProofError("当前平台无法证明受管 systemd 单元已停止")
    run = _default_command_runner if command_runner is None else command_runner
    read_events = _read_cgroup_events if cgroup_events_reader is None else cgroup_events_reader
    if not callable(run) or not callable(read_events):
        raise LegacyWorkerStopProofError("受管单元停机证明适配器不可用")
    active, sub, control_group, restart = _read_unit_state(run, service_unit)
    if active != "inactive" or sub != "dead":
        raise LegacyWorkerStopProofError("受管服务未完全停止")
    if restart != restart_expected:
        raise LegacyWorkerStopProofError("受管服务自动重启策略不符合维护要求")
    if control_group == "":
        return
    events_path = _cgroup_events_path(cgroup_root, control_group, service_unit)
    try:
        populated = parse_cgroup_events(read_events(events_path))
    except FileNotFoundError:
        return
    except MemoryError:
        raise
    except Exception as error:
        raise LegacyWorkerStopProofError("无法确认受管服务 cgroup 已清空") from error
    if populated:
        raise LegacyWorkerStopProofError("受管服务 cgroup 仍有存活进程")


def _validate_service_unit(value: object) -> str:
    """只允许单段、安全且精确的 systemd `.service` 单元名。"""
    if type(value) is not str or _SERVICE_UNIT_NAME.fullmatch(value) is None:
        raise LegacyWorkerStopProofError("受管 systemd unit 名称无效")
    return value


def _validate_expected_restart(value: object) -> str:
    """拒绝控制字符或非 systemd 策略字面量，避免证明目标被静默放宽。"""
    if type(value) is not str or _RESTART_VALUE.fullmatch(value) is None:
        raise LegacyWorkerStopProofError("受管服务重启策略期望值无效")
    return value


def _read_unit_state(run: CommandRunner, unit: str) -> tuple[str, str, str, str]:
    command = (
        "systemctl",
        "show",
        unit,
        "--property=ActiveState",
        "--property=SubState",
        "--property=ControlGroup",
        "--property=Restart",
    )
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except MemoryError:
        raise
    except Exception as error:
        raise LegacyWorkerStopProofError("无法读取受管服务 systemd 状态") from error
    returncode = getattr(result, "returncode", None)
    output = getattr(result, "stdout", None)
    if type(returncode) is not int or returncode != 0 or type(output) is not str:
        raise LegacyWorkerStopProofError("受管服务 systemd 状态不可用")
    expected = {"ActiveState", "SubState", "ControlGroup", "Restart"}
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values or "\x00" in value:
            raise LegacyWorkerStopProofError("受管服务 systemd 状态格式无效")
        values[key] = value
    if set(values) != expected:
        raise LegacyWorkerStopProofError("受管服务 systemd 状态格式无效")
    return (
        values["ActiveState"],
        values["SubState"],
        values["ControlGroup"],
        values["Restart"],
    )


def _cgroup_events_path(root: Path, control_group: str, unit: str) -> Path:
    if not isinstance(root, Path) or not root.is_absolute():
        raise LegacyWorkerStopProofError("cgroup 根路径无效")
    parts = control_group.split("/")
    if not parts or parts[0] != "" or len(parts) < 2:
        raise LegacyWorkerStopProofError("受管服务 ControlGroup 无效")
    segments = parts[1:]
    if segments[-1] != unit or any(
        segment in {".", ".."} or _CONTROL_GROUP_PART.fullmatch(segment) is None
        for segment in segments
    ):
        raise LegacyWorkerStopProofError("受管服务 ControlGroup 无效")
    return root.joinpath(*segments, "cgroup.events")


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


def _read_cgroup_events(path: Path) -> bytes:
    with path.open("rb") as stream:
        return stream.read(4097)


__all__ = [
    "LegacyWorkerStopProofError",
    "verify_codev_codegraph_runtime_masked_stopped",
    "verify_codev_codegraph_running",
    "verify_codev_codegraph_stopped",
    "verify_codev_reindex_stopped",
    "verify_systemd_unit_stopped",
]
