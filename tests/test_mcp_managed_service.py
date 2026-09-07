"""MCP HTTP 写端点的 systemd 固定归属门禁。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


class _拒绝启动(RuntimeError):
    pass


def test_systemd存在时只接受固定unit(monkeypatch, tmp_path) -> None:
    from codev_platform import mcp_managed_service as managed

    runtime_directory = tmp_path / "systemd"
    runtime_directory.mkdir()
    cgroup = tmp_path / "cgroup"
    cgroup.write_bytes(b"0::/system.slice/codev-mcp-graph.service\n")
    monkeypatch.setattr(managed, "_SYSTEMD_RUNTIME_DIRECTORY", runtime_directory)
    monkeypatch.setattr(managed, "_SELF_CGROUP_PATH", cgroup)
    monkeypatch.setattr(managed, "_is_linux", lambda: True)

    assert managed.require_managed_mcp_service("graph") == "codev-mcp-graph.service"

    cgroup.write_bytes(b"0::/user.slice/user-1000.slice/session-1.scope\n")
    with pytest.raises(RuntimeError, match="固定 systemd unit"):
        managed.require_managed_mcp_service("graph")


def test_systemd目录读取异常时失败关闭(monkeypatch, tmp_path) -> None:
    from codev_platform import mcp_managed_service as managed

    def _无法读取(_path: Path):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "lstat", _无法读取)
    monkeypatch.setattr(managed, "_is_linux", lambda: True, raising=False)
    monkeypatch.setattr(
        managed,
        "_SYSTEMD_RUNTIME_DIRECTORY",
        tmp_path / "systemd",
        raising=False,
    )

    assert managed.managed_mcp_service_required() is True


@pytest.mark.parametrize(
    ("kind", "unit"),
    [
        ("chroma", "codev-mcp-platform-docs.service"),
        ("codegraph", "codev-mcp-codegraph.service"),
        ("agent_memory", "codev-mcp-agent-memory.service"),
        ("graph", "codev-mcp-graph.service"),
    ],
)
def test_MCP端点种类映射到唯一固定unit(kind, unit) -> None:
    from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind

    assert mcp_systemd_unit_for_kind(kind) == unit


def test_cgroup路径不接受点段伪造的unit前缀() -> None:
    from codev_platform.core.systemd_process_identity import (
        SystemdProcessIdentityError,
        cgroup_belongs_to_systemd_unit,
    )

    with pytest.raises(SystemdProcessIdentityError, match="格式"):
        cgroup_belongs_to_systemd_unit(
            b"0::/system.slice/codev-mcp-graph.service/../attacker.scope\n",
            "codev-mcp-graph.service",
        )


def test_agent_memory_HTTP启动首先验证systemd归属(monkeypatch) -> None:
    pytest.importorskip("mcp")
    from codev_platform.agent import memory_mcp
    from codev_platform.core import config

    monkeypatch.setattr(
        memory_mcp,
        "require_managed_mcp_service",
        lambda _kind: (_ for _ in ()).throw(_拒绝启动()),
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: pytest.fail("systemd 归属门禁必须早于配置和数据库初始化"),
    )

    with pytest.raises(_拒绝启动):
        asyncio.run(memory_mcp.run_http(18087))


def test_graph_HTTP启动首先验证systemd归属(monkeypatch) -> None:
    pytest.importorskip("mcp")
    from codev_platform.core import config
    from codev_platform.graph import mcp_server

    monkeypatch.setattr(
        mcp_server,
        "require_managed_mcp_service",
        lambda _kind: (_ for _ in ()).throw(_拒绝启动()),
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "load_config",
        lambda: pytest.fail("systemd 归属门禁必须早于配置和图库初始化"),
    )

    with pytest.raises(_拒绝启动):
        asyncio.run(mcp_server.run_http(18092))


def test_platform_docs_HTTP预热前验证systemd归属(monkeypatch) -> None:
    pytest.importorskip("mcp")
    from codev_platform.chroma import server

    monkeypatch.setattr(
        server,
        "require_managed_mcp_service",
        lambda _kind: (_ for _ in ()).throw(_拒绝启动()),
        raising=False,
    )
    monkeypatch.setattr(
        server,
        "_ensure_project",
        lambda *_args: pytest.fail("systemd 归属门禁必须早于模型和 Chroma 预热"),
    )
    monkeypatch.setattr(server.sys, "argv", ["server", "--http"])

    with pytest.raises(_拒绝启动):
        asyncio.run(server.main())
