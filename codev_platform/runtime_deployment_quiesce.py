"""数据库迁移前的固定写入者停机与 cgroup 证明。"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.cgroup_events import parse_cgroup_events


_WRITER_UNITS = (
    "codev-webhook.service",
    "codev-memory-maintenance.timer",
    "codev-memory-maintenance.service",
    "codev-web.service",
    "codev-agent.service",
    "codev-mcp-platform-docs.service",
    "codev-mcp-agent-memory.service",
    "codev-mcp-graph.service",
)
_SERVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*\.(?:service|timer)\Z")
_CGROUP_PART = re.compile(r"[A-Za-z0-9_.@:-]+\Z")
_CGROUP_ROOT = Path("/sys/fs/cgroup")


class RuntimeQuiesceError(RuntimeError):
    """写入者无法全部收敛到可证明的停机边界。"""


@dataclass(frozen=True, slots=True)
class QuiescedUnitState:
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    control_group: str


@dataclass(frozen=True, slots=True)
class RuntimeQuiescePorts:
    stop_unit: Callable[[str], None]
    read_unit: Callable[[str], QuiescedUnitState]
    read_cgroup_events: Callable[[Path], bytes]
    prepare_reindex: Callable[[], None]
    inspect_reindex: Callable[[], None]
    prove_no_unmanaged_mcp_writers: Callable[[], None]

    def validate(self) -> RuntimeQuiescePorts:
        if type(self) is not RuntimeQuiescePorts or not all(
            callable(item)
            for item in (
                self.stop_unit,
                self.read_unit,
                self.read_cgroup_events,
                self.prepare_reindex,
                self.inspect_reindex,
                self.prove_no_unmanaged_mcp_writers,
            )
        ):
            raise RuntimeQuiesceError("停写端口不可用")
        return self


def quiesce_database_writers(
    *,
    ports: RuntimeQuiescePorts | None = None,
    platform_name: str | None = None,
) -> None:
    """先关入口和普通写服务，再进入索引维护态，最后复核全部边界。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        for unit in _WRITER_UNITS:
            active.stop_unit(unit)
        _prove_writer_units(active)
        active.prove_no_unmanaged_mcp_writers()
        active.prepare_reindex()
        active.inspect_reindex()
        _prove_writer_units(active)
        active.prove_no_unmanaged_mcp_writers()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeQuiesceError:
        raise
    except Exception:
        raise RuntimeQuiesceError("数据库写入者停机失败") from None


def verify_database_writers_quiesced(
    *,
    ports: RuntimeQuiescePorts | None = None,
    platform_name: str | None = None,
) -> None:
    """续跑前重新证明维护门禁和所有非索引写入者仍保持停止。"""
    _require_linux(platform_name)
    active = default_ports() if ports is None else ports.validate()
    try:
        active.inspect_reindex()
        _prove_writer_units(active)
        active.prove_no_unmanaged_mcp_writers()
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeQuiesceError:
        raise
    except Exception:
        raise RuntimeQuiesceError("数据库写入者停机状态无法证明") from None


def _prove_writer_units(ports: RuntimeQuiescePorts) -> None:
    for unit in _WRITER_UNITS:
        state = ports.read_unit(unit)
        if type(state) is not QuiescedUnitState:
            raise RuntimeQuiesceError("受管写入单元状态无效")
        if state.load_state == "not-found":
            if (
                state.active_state != "inactive"
                or state.sub_state != "dead"
                or state.main_pid != 0
                or state.control_group != ""
            ):
                raise RuntimeQuiesceError("未安装写入单元仍存在活动状态")
            continue
        if state.load_state not in {"loaded", "masked"}:
            raise RuntimeQuiesceError("受管写入单元加载状态无效")
        if state.active_state != "inactive" or state.sub_state != "dead" or state.main_pid != 0:
            raise RuntimeQuiesceError("受管写入单元未完全停止")
        if state.control_group:
            events_path = _cgroup_events_path(state.control_group, unit)
            try:
                populated = parse_cgroup_events(ports.read_cgroup_events(events_path))
            except FileNotFoundError:
                populated = False
            except Exception:
                raise RuntimeQuiesceError("受管写入单元 cgroup 无法证明") from None
            if populated:
                raise RuntimeQuiesceError("受管写入单元 cgroup 仍有存活进程")


def default_ports() -> RuntimeQuiescePorts:
    from codev_platform.ops.reindex_maintenance import (
        inspect_reindex_maintenance,
        prepare_reindex_maintenance,
    )
    from codev_platform.runtime_unmanaged_mcp_writer import (
        assert_no_unmanaged_mcp_writers,
    )

    return RuntimeQuiescePorts(
        stop_unit=_stop_unit,
        read_unit=_read_unit,
        read_cgroup_events=lambda path: path.read_bytes(),
        prepare_reindex=prepare_reindex_maintenance,
        inspect_reindex=inspect_reindex_maintenance,
        prove_no_unmanaged_mcp_writers=assert_no_unmanaged_mcp_writers,
    )


def _stop_unit(unit: str) -> None:
    name = _require_unit(unit)
    before = _read_unit(name)
    if before.load_state == "not-found":
        return
    result = subprocess.run(
        ("systemctl", "stop", name),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=30.0,
    )
    if result.returncode != 0:
        raise RuntimeQuiesceError("受管写入单元停止命令失败")


def _read_unit(unit: str) -> QuiescedUnitState:
    name = _require_unit(unit)
    result = subprocess.run(
        (
            "systemctl",
            "show",
            name,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=MainPID",
            "--property=ControlGroup",
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=10.0,
    )
    if result.returncode != 0:
        raise RuntimeQuiesceError("受管写入单元状态不可读取")
    expected = {"LoadState", "ActiveState", "SubState", "MainPID", "ControlGroup"}
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values or "\x00" in value:
            raise RuntimeQuiesceError("受管写入单元状态格式无效")
        values[key] = value
    if set(values) != expected or not values["MainPID"].isdigit():
        raise RuntimeQuiesceError("受管写入单元状态字段不完整")
    return QuiescedUnitState(
        values["LoadState"],
        values["ActiveState"],
        values["SubState"],
        int(values["MainPID"]),
        values["ControlGroup"],
    )


def _cgroup_events_path(control_group: str, unit: str) -> Path:
    parts = control_group.split("/")
    segments = parts[1:] if parts[:1] == [""] else []
    if (
        len(segments) < 2
        or segments[-1] != unit
        or any(
            segment in {"", ".", ".."} or _CGROUP_PART.fullmatch(segment) is None
            for segment in segments
        )
    ):
        raise RuntimeQuiesceError("受管写入单元 ControlGroup 无效")
    return _CGROUP_ROOT.joinpath(*segments, "cgroup.events")


def _require_unit(value: object) -> str:
    if (
        type(value) is not str
        or value not in _WRITER_UNITS
        or _SERVICE_NAME.fullmatch(value) is None
    ):
        raise RuntimeQuiesceError("写入单元不在固定注册表中")
    return value


def _require_linux(platform_name: str | None) -> None:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise RuntimeQuiesceError("停写操作只支持 Linux systemd")


__all__ = [
    "QuiescedUnitState",
    "RuntimeQuiesceError",
    "RuntimeQuiescePorts",
    "quiesce_database_writers",
    "verify_database_writers_quiesced",
]
