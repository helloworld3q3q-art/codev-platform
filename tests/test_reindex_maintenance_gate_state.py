"""维护门禁状态机的跨平台回归契约。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_维护状态机按顺序完成恢复交接(tmp_path: Path) -> None:
    """只有已绑定的待命实例才能在最后移除 marker。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    invocation_id = "a" * 32

    maintenance_gate.activate_maintenance_gate(path=marker)
    assert maintenance_gate.read_maintenance_gate_record(path=marker).phase == "maintenance"

    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    assert armed.phase == "restore_armed"
    assert maintenance_gate.read_maintenance_gate_record(path=marker) == armed

    claimed = maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    assert claimed.phase == "restore_claimed"
    assert claimed.invocation_id == invocation_id
    assert maintenance_gate.read_maintenance_gate_record(path=marker) == claimed

    maintenance_gate.complete_restore_standby(
        generation=claimed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    assert marker.exists() is False
    assert maintenance_gate.maintenance_gate_active(path=marker) is False


def test_错误generation不能声明待命且保留marker(tmp_path: Path) -> None:
    """错误 generation 不得把已预备状态绑定到其他实例。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    wrong_generation = "0" * 32 if armed.generation != "0" * 32 else "1" * 32

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.claim_restore_standby(
            generation=wrong_generation,
            invocation_id="a" * 32,
            path=marker,
        )

    assert marker.exists() is True
    assert maintenance_gate.read_maintenance_gate_record(path=marker) == armed


def test_错误invocation不能完成交接且保留marker(tmp_path: Path) -> None:
    """错误 InvocationID 不得删除属于当前待命实例的 marker。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    invocation_id = "a" * 32
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    claimed = maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.complete_restore_standby(
            generation=claimed.generation,
            invocation_id="b" * 32,
            path=marker,
        )

    assert marker.exists() is True
    assert maintenance_gate.read_maintenance_gate_record(path=marker) == claimed


def test_已声明待命可在同一身份下续租(tmp_path: Path) -> None:
    """慢恢复只能延长同一 generation/InvocationID，不能换实例。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    invocation_id = "a" * 32
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    claimed = maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    renewed = maintenance_gate.renew_claimed_restore_standby(
        generation=claimed.generation,
        invocation_id=invocation_id,
        ttl_sec=60.0,
        path=marker,
    )

    assert renewed.phase == "restore_claimed"
    assert renewed.generation == claimed.generation
    assert renewed.invocation_id == invocation_id
    assert renewed.expires_at > claimed.expires_at


def test_已持转换锁原语续租后可在同一临界区最终交接(tmp_path: Path) -> None:
    """组合状态机必须能在外层已持 EX 时操作 marker，不能再次取得同一 flock。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    invocation_id = "a" * 32
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    claimed = maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
        patch.setattr(maintenance_gate, "_is_linux", lambda: False)
        with maintenance_gate.maintenance_systemd_transition_lock():
            renewed = (
                maintenance_gate.renew_claimed_restore_standby_while_systemd_transition_locked(
                    generation=claimed.generation,
                    invocation_id=invocation_id,
                    ttl_sec=60.0,
                )
            )
            maintenance_gate.complete_restore_standby_while_systemd_transition_locked(
                generation=renewed.generation,
                invocation_id=invocation_id,
            )

    assert renewed.expires_at > claimed.expires_at
    assert marker.exists() is False


def test_锁内状态原语脱离真实转换守卫时拒绝写入(tmp_path: Path) -> None:
    """while-locked 名称不是能力，直接调用不能续租或删除默认 marker。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    invocation_id = "a" * 32
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    claimed = maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
        path=marker,
    )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(maintenance_gate, "_DEFAULT_MARKER_PATH", marker)
        with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换守卫"):
            maintenance_gate.complete_restore_standby_while_systemd_transition_locked(
                generation=claimed.generation,
                invocation_id=invocation_id,
            )

    assert marker.exists() is True


def test_admin窗口退出失败保留原始锁异常而不二次yield(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已向调用方交付许可后，锁退出异常必须原样传播。"""
    from codev_platform.reindex import maintenance_gate, maintenance_gate_state
    from codev_platform.reindex.maintenance_gate_record import maintenance_record

    class ExitFailure:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            raise maintenance_gate.MaintenanceGateError("转换锁退出失败")

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_systemd_transition_lock",
        lambda: ExitFailure(),
    )
    monkeypatch.setattr(
        maintenance_gate_state,
        "read_maintenance_gate_record",
        lambda: maintenance_record(),
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError, match="转换锁退出失败"):
        with maintenance_gate.maintenance_admin_window_permit() as permitted:
            assert permitted is True


def test_过期待命不能声明且保留marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待命租约到期后必须保持 marker，不能自动放开写入。"""
    from codev_platform.reindex import maintenance_gate, maintenance_gate_state

    marker = tmp_path / "reindex-maintenance.gate"
    maintenance_gate.activate_maintenance_gate(path=marker)
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    monkeypatch.setattr(
        maintenance_gate_state.time,
        "time",
        lambda: float(armed.expires_at),
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.claim_restore_standby(
            generation=armed.generation,
            invocation_id="a" * 32,
            path=marker,
        )

    assert marker.exists() is True
    assert maintenance_gate.read_maintenance_gate_record(path=marker) == armed


def test_marker存在的任意状态均拒绝通用写许可(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """维护、预备和已声明待命都不能使通用写入口放行。"""
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"

    maintenance_gate.activate_maintenance_gate(path=marker)
    with monkeypatch.context() as linux_gate:
        linux_gate.setattr(maintenance_gate, "_is_linux", lambda: True)
        linux_gate.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
        assert maintenance_gate.maintenance_gate_allows_current_worker() is False

    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0, path=marker)
    with monkeypatch.context() as linux_gate:
        linux_gate.setattr(maintenance_gate, "_is_linux", lambda: True)
        linux_gate.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
        assert maintenance_gate.maintenance_gate_allows_current_worker() is False

    maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id="a" * 32,
        path=marker,
    )
    with monkeypatch.context() as linux_gate:
        linux_gate.setattr(maintenance_gate, "_is_linux", lambda: True)
        linux_gate.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
        assert maintenance_gate.maintenance_gate_allows_current_worker() is False


def test_restore待命许可在全局共享锁内读取marker(monkeypatch) -> None:
    """arm/claim 的耐久替换不能让 service 读到中间状态后误退出。"""
    from codev_platform.reindex import maintenance_gate, maintenance_gate_state
    from codev_platform.reindex.maintenance_gate_record import restore_armed_record

    events: list[object] = []
    record = restore_armed_record(generation="a" * 32, expires_at=4_102_444_800)
    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: True)
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
    monkeypatch.setattr(maintenance_gate, "_current_process_is_reindex_unit", lambda: True)
    monkeypatch.setattr(
        maintenance_gate,
        "_open_global_reader_gate_lock",
        lambda: events.append(("open-reader",)) or 7,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "_release_linux_lock",
        lambda descriptor: events.append(("release", descriptor)),
    )
    monkeypatch.setattr(maintenance_gate_state, "read_maintenance_gate_record", lambda: record)

    assert maintenance_gate_state.maintenance_restore_standby_permit() is True
    assert events == [("open-reader",), ("release", 7)]
