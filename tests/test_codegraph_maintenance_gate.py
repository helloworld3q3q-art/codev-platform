"""CodeGraph 维护门禁的独立行为测试。"""
from __future__ import annotations

import pytest

def test_维护marker明确启用时禁止启动CodeGraph(monkeypatch) -> None:
    """维护标记存在时，HTTP 与脱离式启动入口都必须失败关闭。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: True)

    assert codegraph_gate.codegraph_start_permitted() is False


def test_维护marker无法证明时禁止启动CodeGraph(monkeypatch) -> None:
    """读取 marker 或锁发生异常不能被当作未维护。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    def _无法读取marker():
        raise RuntimeError("维护锁不可读取")

    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", _无法读取marker)

    assert codegraph_gate.codegraph_start_permitted() is False


def test_请求与后端启动许可不申请全局维护锁(monkeypatch) -> None:
    """异步热路径只读 marker，绝不能等待会阻塞转换的共享 flock。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "_maintenance_operation_permit",
        lambda: pytest.fail("CodeGraph 异步门禁不得申请全局维护锁"),
        raising=False,
    )

    assert codegraph_gate.codegraph_request_permitted() is True
    assert codegraph_gate.codegraph_backend_start_permitted() is True


def _解析结果(module, *, fragment_path: str, load_state: str = "loaded", unit_file_state: str = "enabled"):
    return module.SystemdUnitResolution(
        unit_name="codev-mcp-codegraph.service",
        load_state=load_state,
        unit_file_state=unit_file_state,
        fragment_path=fragment_path,
    )


def test_marker已删除但runtime残留与实际解析不一致时禁止启动CodeGraph(monkeypatch) -> None:
    """/run 文件存在不是 mask 真值；不一致状态仍须失败关闭。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "read_unit_resolution",
        lambda _name: _解析结果(
            codegraph_gate,
            fragment_path="/etc/systemd/system/codev-mcp-codegraph.service",
        ),
    )
    monkeypatch.setattr(codegraph_gate, "read_runtime_mask_target", lambda _name: "/dev/null")

    assert codegraph_gate.codegraph_start_permitted() is False


def test_runtime_mask状态不可证明时禁止启动CodeGraph(monkeypatch) -> None:
    """systemctl 解析异常不能被误判为 runtime hold 已解除。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "read_unit_resolution",
        lambda _name: (_ for _ in ()).throw(RuntimeError("systemctl 不可读取")),
    )

    assert codegraph_gate.codegraph_start_permitted() is False


def test_实际未mask时允许正常启动许可(monkeypatch) -> None:
    """仅 systemd 确认未 mask 时，才把控制权交给共享维护许可。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "read_unit_resolution",
        lambda _name: _解析结果(
            codegraph_gate,
            fragment_path="/usr/local/lib/systemd/system/codev-mcp-codegraph.service",
        ),
    )
    monkeypatch.setattr(codegraph_gate, "read_runtime_mask_target", lambda _name: None)

    assert codegraph_gate.codegraph_start_permitted() is True


def test_请求许可只读取维护marker而不执行systemd解析(monkeypatch) -> None:
    """MCP/HTTP 热路径不得因短暂 D-Bus 抖动反复启动 systemctl 子进程。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "read_unit_resolution",
        lambda _name: pytest.fail("请求许可不得查询 systemd"),
    )

    assert codegraph_gate.codegraph_request_permitted() is True


def test_受管服务启动校验不申请共享维护许可(monkeypatch) -> None:
    """转换锁内 systemctl start 触发服务启动时不能反向等待同一把共享锁。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate
    from codev_platform.reindex import external_worker_guard, maintenance_gate as reindex_gate

    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(reindex_gate, "maintenance_gate_active", lambda: False)
    monkeypatch.setattr(codegraph_gate, "_runtime_mask_active", lambda: False)
    monkeypatch.setattr(
        codegraph_gate,
        "_maintenance_operation_permit",
        lambda: pytest.fail("服务启动校验不得申请共享维护许可"),
        raising=False,
    )
    monkeypatch.setattr(
        external_worker_guard,
        "current_process_in_systemd_unit_cgroup",
        lambda _unit: True,
    )

    assert codegraph_gate.codegraph_service_start_permitted() is True


def test_Linux检测到systemd时要求使用受管CodeGraph服务(monkeypatch) -> None:
    """服务化环境不允许 serve-mcp 创建脱离 systemd 的 Python 代理。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)

    assert codegraph_gate.codegraph_requires_managed_service() is True


def test_无systemd环境仍允许兼容的脱离式CodeGraph启动(monkeypatch) -> None:
    """Windows 与无 systemd 的开发环境不应被生产运行策略误伤。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: False)

    assert codegraph_gate.codegraph_requires_managed_service() is False


def test_systemd状态不可证明时仍要求使用受管服务(monkeypatch) -> None:
    """Linux 的运行态目录读失败时不得降级创建 detached Python 代理。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate

    monkeypatch.setattr(codegraph_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(
        codegraph_gate,
        "_SYSTEMD_RUNTIME_DIRECTORY",
        type("不可读取的systemd目录", (), {"lstat": lambda _self: (_ for _ in ()).throw(OSError())})(),
    )

    assert codegraph_gate.codegraph_requires_managed_service() is True


def test_systemd下手工CodeGraph进程不属于固定unit时拒绝服务启动(monkeypatch) -> None:
    """runtime mask 解除后，手工 Python 代理也不能绕过固定 systemd unit。"""
    from codev_platform.codegraph import maintenance_gate as codegraph_gate
    from codev_platform.reindex import external_worker_guard

    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(codegraph_gate, "_runtime_mask_active", lambda: False)
    monkeypatch.setattr(
        external_worker_guard,
        "current_process_in_systemd_unit_cgroup",
        lambda unit: unit == "codev-mcp-codegraph.service" and False,
    )

    assert codegraph_gate.codegraph_service_start_permitted() is False
    with pytest.raises(codegraph_gate.CodegraphMaintenanceGateError):
        codegraph_gate.require_codegraph_service_start_permitted()


def test_systemd下固定CodeGraph_unit中的进程可通过服务身份检查(monkeypatch) -> None:
    from codev_platform.codegraph import maintenance_gate as codegraph_gate
    from codev_platform.reindex import external_worker_guard

    calls: list[str] = []
    monkeypatch.setattr(codegraph_gate, "_systemd_detected", lambda: True)
    monkeypatch.setattr(codegraph_gate, "_maintenance_marker_active", lambda: False)
    monkeypatch.setattr(codegraph_gate, "_runtime_mask_active", lambda: False)
    monkeypatch.setattr(
        external_worker_guard,
        "current_process_in_systemd_unit_cgroup",
        lambda unit: calls.append(unit) or True,
    )

    assert codegraph_gate.codegraph_service_start_permitted() is True
    assert calls == ["codev-mcp-codegraph.service"]
