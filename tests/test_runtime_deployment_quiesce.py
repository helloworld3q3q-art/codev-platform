"""正式部署数据库写入者停机边界测试。"""

from __future__ import annotations

import pytest

import codev_platform.runtime_deployment_quiesce as quiesce_module
from codev_platform.runtime_deployment_quiesce import (
    QuiescedUnitState,
    RuntimeQuiesceError,
    RuntimeQuiescePorts,
    quiesce_database_writers,
    verify_database_writers_quiesced,
)


_UNITS = (
    "codev-webhook.service",
    "codev-memory-maintenance.timer",
    "codev-memory-maintenance.service",
    "codev-web.service",
    "codev-agent.service",
    "codev-mcp-platform-docs.service",
    "codev-mcp-agent-memory.service",
    "codev-mcp-graph.service",
)


def _ports(events: list[str], *, populated: str | None = None) -> RuntimeQuiescePorts:
    def stop(unit: str) -> None:
        events.append(f"stop:{unit}")

    def read(unit: str) -> QuiescedUnitState:
        events.append(f"read:{unit}")
        group = f"/system.slice/{unit}" if unit == populated else ""
        return QuiescedUnitState("loaded", "inactive", "dead", 0, group)

    return RuntimeQuiescePorts(
        stop_unit=stop,
        read_unit=read,
        read_cgroup_events=lambda _path: b"populated 1\nfrozen 0\n",
        prepare_reindex=lambda: events.append("reindex:prepare"),
        inspect_reindex=lambda: events.append("reindex:inspect"),
        prove_no_unmanaged_mcp_writers=lambda: events.append("unmanaged:prove"),
    )


def test_停写顺序先关闭入口再准备索引维护并二次证明() -> None:
    events: list[str] = []

    quiesce_database_writers(ports=_ports(events), platform_name="linux")

    stops = [f"stop:{unit}" for unit in _UNITS]
    assert events[: len(stops)] == stops
    prepare = events.index("reindex:prepare")
    assert prepare > max(events.index(f"read:{unit}") for unit in _UNITS)
    assert events[prepare + 1] == "reindex:inspect"
    assert events.count("unmanaged:prove") == 2
    assert events.index("unmanaged:prove") < prepare
    assert events[-1] == "unmanaged:prove"
    for unit in _UNITS:
        assert events.count(f"read:{unit}") == 2


def test_稳定观测产物写入服务全部属于停写注册表() -> None:
    from codev_platform.runtime_deployment_guard import GUARDED_SYSTEMD_UNITS

    assert quiesce_module._WRITER_UNITS == _UNITS
    assert set(_UNITS) <= set(GUARDED_SYSTEMD_UNITS)


def test_续跑证明不重复stop或prepare() -> None:
    events: list[str] = []

    verify_database_writers_quiesced(ports=_ports(events), platform_name="linux")

    assert events[0] == "reindex:inspect"
    assert events[-1] == "unmanaged:prove"
    assert not any(item.startswith("stop:") for item in events)
    assert "reindex:prepare" not in events


def test_任一写入者cgroup仍填充时失败关闭() -> None:
    events: list[str] = []

    with pytest.raises(RuntimeQuiesceError, match="存活进程"):
        quiesce_database_writers(
            ports=_ports(events, populated="codev-web.service"),
            platform_name="linux",
        )

    assert "reindex:prepare" not in events


def test_未安装单元只接受完全无活动状态() -> None:
    ports = _ports([])
    ports = RuntimeQuiescePorts(
        stop_unit=ports.stop_unit,
        read_unit=lambda _unit: QuiescedUnitState("not-found", "inactive", "dead", 0, ""),
        read_cgroup_events=ports.read_cgroup_events,
        prepare_reindex=ports.prepare_reindex,
        inspect_reindex=ports.inspect_reindex,
        prove_no_unmanaged_mcp_writers=ports.prove_no_unmanaged_mcp_writers,
    )

    quiesce_database_writers(ports=ports, platform_name="linux")


def test_拒绝非Linux和注册表外单元状态() -> None:
    with pytest.raises(RuntimeQuiesceError, match="Linux"):
        quiesce_database_writers(ports=_ports([]), platform_name="win32")

    with pytest.raises(RuntimeQuiesceError):
        quiesce_module._require_unit("evil.service")


def test_发现systemd外MCP写端点时不进入索引维护() -> None:
    events: list[str] = []
    ports = _ports(events)
    ports = RuntimeQuiescePorts(
        stop_unit=ports.stop_unit,
        read_unit=ports.read_unit,
        read_cgroup_events=ports.read_cgroup_events,
        prepare_reindex=ports.prepare_reindex,
        inspect_reindex=ports.inspect_reindex,
        prove_no_unmanaged_mcp_writers=lambda: (_ for _ in ()).throw(RuntimeError("unmanaged")),
    )

    with pytest.raises(RuntimeQuiesceError, match="停机失败"):
        quiesce_database_writers(ports=ports, platform_name="linux")

    assert "reindex:prepare" not in events
