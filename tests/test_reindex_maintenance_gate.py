"""reindex 维护门禁与 detached launcher 的回归契约。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path

import pytest

from codev_platform.reindex.queue import FileSpoolQueue
from tests.reindex_maintenance_gate_test_support import (
    _bind_linux_global_gate,
    _bind_test_marker,
    _launcher_ports,
)


def test_维护门禁可原子启用并幂等移除(tmp_path: Path) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"

    assert maintenance_gate.maintenance_gate_active(path=marker) is False

    maintenance_gate.activate_maintenance_gate(path=marker)

    assert maintenance_gate.maintenance_gate_active(path=marker) is True

    maintenance_gate.deactivate_maintenance_gate(path=marker)
    maintenance_gate.deactivate_maintenance_gate(path=marker)

    assert maintenance_gate.maintenance_gate_active(path=marker) is False


def test_维护门禁损坏时仍视为已启用(tmp_path: Path) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    marker.write_bytes(b"not-a-valid-maintenance-marker")

    assert maintenance_gate.maintenance_gate_active(path=marker) is True


def test_维护门禁读取元数据失败时仍视为已启用(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    original_lstat = Path.lstat

    def deny_marker_metadata(path: Path):
        if path == marker:
            raise PermissionError("拒绝读取 marker 元数据")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_marker_metadata)

    assert maintenance_gate.maintenance_gate_active(path=marker) is True


def test_维护门禁写入失败时保留已有门禁(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    marker.write_bytes(b"damaged-but-still-blocking")

    def fail_publish(_path: Path, _payload: bytes) -> None:
        raise OSError("模拟原子发布失败")

    monkeypatch.setattr(maintenance_gate, "durable_write_replace", fail_publish)

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.activate_maintenance_gate(path=marker)

    assert maintenance_gate.maintenance_gate_active(path=marker) is True


def test_维护门禁移除失败时保留门禁(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    marker.write_bytes(b"still-active")

    def fail_remove(_path: Path) -> bool:
        raise OSError("模拟原子移除失败")

    monkeypatch.setattr(maintenance_gate, "durable_unlink", fail_remove)

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.deactivate_maintenance_gate(path=marker)

    assert maintenance_gate.maintenance_gate_active(path=marker) is True


def test_supervisor重导出维护门禁契约() -> None:
    from codev_platform.reindex import maintenance_gate, supervisor

    assert supervisor.activate_maintenance_gate is maintenance_gate.activate_maintenance_gate
    assert supervisor.deactivate_maintenance_gate is maintenance_gate.deactivate_maintenance_gate
    assert supervisor.maintenance_gate_active is maintenance_gate.maintenance_gate_active


def test_默认写许可固定按intent共享_gate共享顺序准入(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []
    default_marker = Path("C:/codev-platform/reindex-maintenance.gate")
    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", default_marker)
    monkeypatch.setattr(
        maintenance_gate,
        "_marker_path",
        lambda _path: default_marker,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "_open_global_transition_intent_lock",
        lambda *, exclusive: (
            events.append(f"intent-{'exclusive' if exclusive else 'shared'}") or 11
        ),
        raising=False,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "_open_global_linux_lock",
        lambda *, exclusive: events.append(f"gate-{'exclusive' if exclusive else 'shared'}") or 22,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "_release_linux_lock",
        lambda descriptor: events.append(f"release-{descriptor}"),
    )
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_gate_active",
        lambda **_kwargs: events.append("marker-proof") or False,
    )

    with maintenance_gate._worker_start_permit() as permitted:
        assert permitted is True
        events.append("body")

    assert events == [
        "intent-shared",
        "gate-shared",
        "release-11",
        "marker-proof",
        "body",
        "release-22",
    ]


def test_systemd转换固定按intent独占_gate独占顺序执行(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    events: list[str] = []

    @contextmanager
    def gate_lock():
        events.append("gate-enter")
        try:
            yield
        finally:
            events.append("gate-exit")

    @contextmanager
    def intent_lock():
        events.append("intent-enter")
        try:
            yield gate_lock
        finally:
            events.append("intent-exit")

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(
        maintenance_gate,
        "_marker_path",
        lambda _path: Path("C:/codev-platform/reindex-maintenance.gate"),
    )
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_intent",
        intent_lock,
        raising=False,
    )

    with maintenance_gate.maintenance_systemd_transition_lock():
        events.append("body")

    assert events == [
        "intent-enter",
        "gate-enter",
        "body",
        "gate-exit",
        "intent-exit",
    ]


def test_未持转换守卫禁止调用默认锁内marker原语(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """函数名不能充当锁证明，直接调用必须在任何持久化写入前失败。"""
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(
        maintenance_gate,
        "_write_maintenance_gate_record",
        lambda *_args: pytest.fail("缺少转换守卫时不得写 marker"),
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
        maintenance_gate.activate_maintenance_gate_while_systemd_transition_locked()


def test_转换守卫只在真实临界区内有效(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_lock():
        maintenance_gate.require_systemd_transition_guard()

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
        maintenance_gate.require_systemd_transition_guard()


def test_转换会话守卫覆盖嵌套转换并在退出后立即失效(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话锁是管理员串行能力，不替代 intent 或 gate 的精确语义。"""
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_session():
        maintenance_gate.require_systemd_transition_session_guard()
        with maintenance_gate.maintenance_systemd_transition_lock():
            maintenance_gate.require_systemd_transition_session_guard()
            maintenance_gate.require_systemd_transition_intent_guard()
            maintenance_gate.require_systemd_transition_guard()

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换会话守卫"):
        maintenance_gate.require_systemd_transition_session_guard()


def test_复制上下文中的转换守卫在锁退出后立即失效(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """上下文副本不能把已经释放的 flock 能力带到临界区之外。"""
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_lock():
        copied = copy_context()
        copied.run(maintenance_gate.require_systemd_transition_guard)

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
        copied.run(maintenance_gate.require_systemd_transition_guard)


def test_转换意图能力与gate能力按各自临界区生效并及时失效(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_intent() as gate_lock:
        maintenance_gate.require_systemd_transition_intent_guard()
        copied_intent = copy_context()
        with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
            maintenance_gate.require_systemd_transition_guard()

        with gate_lock():
            maintenance_gate.require_systemd_transition_intent_guard()
            maintenance_gate.require_systemd_transition_guard()

        maintenance_gate.require_systemd_transition_intent_guard()
        with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
            maintenance_gate.require_systemd_transition_guard()

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换意图守卫"):
        maintenance_gate.require_systemd_transition_intent_guard()
    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换意图守卫"):
        copied_intent.run(maintenance_gate.require_systemd_transition_intent_guard)


def test_gate工厂逃出intent后在原上下文和复制上下文均不可再次使用(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_intent() as escaped_gate_lock:
        copied_intent = copy_context()

    def acquire_gate() -> None:
        with escaped_gate_lock():
            pytest.fail("失效的 intent 不得再派生 gate 能力")

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换意图守卫"):
        acquire_gate()
    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换意图守卫"):
        copied_intent.run(acquire_gate)


def test_未持转换意图守卫禁止调用提前发布marker原语(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(
        maintenance_gate,
        "_write_maintenance_gate_record",
        lambda *_args: pytest.fail("缺少转换意图守卫时不得写 marker"),
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换意图守卫"):
        maintenance_gate.activate_maintenance_gate_while_systemd_transition_intent_locked()


def test_持有intent但尚未取得gate时允许真实原语耐久发布marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from codev_platform.reindex import maintenance_gate

    writes: list[tuple[object, object]] = []
    marker = (tmp_path / "reindex-maintenance.gate").resolve()
    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)
    monkeypatch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
    monkeypatch.setattr(
        maintenance_gate,
        "_write_maintenance_gate_record",
        lambda path, record: writes.append((path, record)),
    )

    with maintenance_gate.maintenance_systemd_transition_intent():
        maintenance_gate.require_systemd_transition_intent_guard()
        with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
            maintenance_gate.require_systemd_transition_guard()
        maintenance_gate.activate_maintenance_gate_while_systemd_transition_intent_locked()

    assert len(writes) == 1
    assert writes[0][0] == marker


def test_显式默认marker与路径别名始终使用全局锁域(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """调用形式不能决定锁域，最终指向生产 marker 的路径必须统一走 intent。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "codev-platform" / "reindex-maintenance.gate"
    alias = marker.parent / "nested" / ".." / marker.name
    events: list[bool] = []

    @contextmanager
    def capture_lock(current: Path, **legacy: object):
        events.append(
            bool(legacy["global_path"])
            if "global_path" in legacy
            else maintenance_gate._is_default_marker(current)
        )
        yield

    monkeypatch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(maintenance_gate, "_exclusive_gate_lock", capture_lock)
    monkeypatch.setattr(maintenance_gate, "_write_maintenance_gate_record", lambda *_: None)

    maintenance_gate.activate_maintenance_gate(path=marker)
    maintenance_gate.activate_maintenance_gate(path=alias)

    assert maintenance_gate._is_default_marker(alias) is True
    assert events == [True, True]


def test_全局锁退出失败不得覆盖临界区主体异常(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """释放错误只能在主体成功时上报，已有主体错误必须保留为主异常。"""
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_open_global_linux_lock", lambda **_: 7)
    monkeypatch.setattr(
        maintenance_gate,
        "_release_linux_lock",
        lambda _descriptor: (_ for _ in ()).throw(
            maintenance_gate.MaintenanceGateError("释放失败")
        ),
    )

    with maintenance_gate._systemd_transition_intent_guard():
        with pytest.raises(ValueError, match="主体失败"):
            with maintenance_gate._global_systemd_gate_lock():
                raise ValueError("主体失败")


def test_marker存在时禁止自动启动和直接启动(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate, worker_launcher

    marker = tmp_path / "reindex-maintenance.gate"
    maintenance_gate.activate_maintenance_gate(path=marker)
    _bind_test_marker(monkeypatch, marker)

    def unexpected_status() -> dict[str, object]:
        pytest.fail("维护门禁命中后不得读取 worker 状态")

    assert worker_launcher.should_auto_start(FileSpoolQueue(tmp_path / "spool"), {}) is False
    assert worker_launcher.ensure_worker_running(
        {},
        ports=_launcher_ports(worker_status=unexpected_status),
        cwd=tmp_path,
    ) == {"action": "maintenance-gated"}


def test_marker损坏时禁止自动启动和直接启动(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import worker_launcher

    marker = tmp_path / "reindex-maintenance.gate"
    marker.write_bytes(b"damaged")
    _bind_test_marker(monkeypatch, marker)

    assert worker_launcher.should_auto_start(FileSpoolQueue(tmp_path / "spool"), {}) is False
    assert worker_launcher.ensure_worker_running(
        {},
        ports=_launcher_ports(),
        cwd=tmp_path,
    ) == {"action": "maintenance-gated"}


def test_marker不可读时禁止自动启动和直接启动(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate, worker_launcher

    marker = tmp_path / "reindex-maintenance.gate"
    original_lstat = Path.lstat

    def deny_marker_metadata(path: Path):
        if path == marker:
            raise PermissionError("拒绝读取 marker 元数据")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_marker_metadata)
    _bind_test_marker(monkeypatch, marker)

    assert maintenance_gate.maintenance_gate_active(path=marker) is True
    assert worker_launcher.should_auto_start(FileSpoolQueue(tmp_path / "spool"), {}) is False
    assert worker_launcher.ensure_worker_running(
        {},
        ports=_launcher_ports(),
        cwd=tmp_path,
    ) == {"action": "maintenance-gated"}


def test_detached_launcher固定要求isolated执行模式(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate, worker_launcher

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()

    python = tmp_path / "python"
    python.write_bytes(b"")
    spawned: list[list[str]] = []
    recorded: list[dict[str, object]] = []
    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: python)
    monkeypatch.setattr(worker_launcher, "logs_dir", lambda: tmp_path)

    def capture_spawn(command: list[str], *_args, **_kwargs) -> int:
        spawned.append(command)
        return 4321

    ports = _launcher_ports(
        spawn_process=capture_spawn,
        record_spawned=lambda *_args, **kwargs: recorded.append(kwargs),
    )

    result = worker_launcher.ensure_worker_running({}, ports=ports, cwd=tmp_path)

    assert result == {"action": "spawned", "pid": 4321}
    assert spawned[0][:3] == [str(python), "-I", "-m"]
    assert spawned[0][-2:] == ["--require-execution-mode", "isolated"]
    assert recorded == [
        {
            "cwd": str(tmp_path),
            "idle_exit_sec": 600.0,
            "heartbeat_sec": 10.0,
            "cmd": spawned[0],
            "execution_mode": "isolated",
        }
    ]


def test_detached_launcher拒绝legacy配置且不创建子进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate, worker_launcher

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()

    python = tmp_path / "python"
    python.write_bytes(b"")
    monkeypatch.setattr("codev_platform.mcp_serve._platform_runtime_python", lambda: python)

    def reject_spawn(*_args, **_kwargs) -> int:
        pytest.fail("legacy 配置不得创建 detached worker")

    result = worker_launcher.ensure_worker_running(
        {"reindex": {"execution_mode": "legacy"}},
        ports=_launcher_ports(spawn_process=reject_spawn),
        cwd=tmp_path,
    )

    assert result == {
        "action": "fail",
        "error": "detached worker 仅支持 execution_mode=isolated",
    }
