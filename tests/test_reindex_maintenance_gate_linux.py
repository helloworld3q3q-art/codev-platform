"""reindex 维护门禁与 detached launcher 的回归契约。"""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from codev_platform.reindex.queue import FileSpoolQueue
from tests.reindex_maintenance_gate_test_support import (
    _bind_cgroup_proof,
    _bind_linux_global_gate,
    _bind_test_marker,
    _launcher_ports,
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用全局共享维护锁")
def test_linux缺失维护锁时禁止自动启动和直接启动(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import worker_launcher

    marker = tmp_path / "reindex-maintenance.gate"
    _bind_test_marker(monkeypatch, marker)

    assert worker_launcher.should_auto_start(FileSpoolQueue(tmp_path / "spool"), {}) is False
    assert worker_launcher.ensure_worker_running(
        {},
        ports=_launcher_ports(),
        cwd=tmp_path,
    ) == {"action": "maintenance-gated"}


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用全局共享维护锁")
def test_linux维护锁不是普通文件时拒绝启动许可(tmp_path: Path) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    marker.with_name(f".{marker.name}.lock").mkdir()

    with maintenance_gate._worker_start_permit(path=marker) as permitted:
        assert permitted is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用全局共享维护锁")
def test_linux维护锁使activate等待已获得的启动许可(tmp_path: Path) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = tmp_path / "reindex-maintenance.gate"
    maintenance_gate.activate_maintenance_gate(path=marker)
    lock = marker.with_name(f".{marker.name}.lock")
    maintenance_gate.deactivate_maintenance_gate(path=marker)
    assert stat.S_ISREG(lock.stat().st_mode)
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644

    entered = threading.Event()
    completed = threading.Event()
    failures: list[Exception] = []

    def activate_gate() -> None:
        entered.set()
        try:
            maintenance_gate.activate_maintenance_gate(path=marker)
        except Exception as error:
            failures.append(error)
        finally:
            completed.set()

    with maintenance_gate._worker_start_permit(path=marker) as permitted:
        assert permitted is True
        thread = threading.Thread(target=activate_gate)
        thread.start()
        assert entered.wait(1.0)
        time.sleep(0.1)
        assert completed.is_set() is False
    thread.join(2.0)

    assert thread.is_alive() is False
    assert failures == []
    assert maintenance_gate.maintenance_gate_active(path=marker) is True


def test_维护门禁未启用时当前进程准入不读取cgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: False)

    def unexpected_cgroup_probe() -> bool:
        pytest.fail("未启用维护门禁时不得读取 cgroup")

    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        unexpected_cgroup_probe,
    )

    assert maintenance_gate.maintenance_gate_allows_current_worker() is True


@pytest.mark.parametrize(
    "cgroup",
    [
        "/system.slice/codev-reindex.service",
        "/system.slice/codev-reindex.service/worker.scope",
    ],
)
def test_维护门禁启用时受控service_cgroup也不得取得通用写许可(
    cgroup: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
    cgroup_path = tmp_path / "cgroup"
    cgroup_path.write_text(f"0::{cgroup}\n", encoding="utf-8")
    _bind_cgroup_proof(monkeypatch, cgroup_path)

    assert maintenance_gate.maintenance_gate_allows_current_worker() is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_restore待命状态只允许service等待而不允许通用写操作(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """restore 期间 unit 可以待命，但任何通用 reindex 写入口都必须拒绝。"""
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.activate_maintenance_gate()
    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        lambda: True,
    )

    record = maintenance_gate.arm_restore_standby(ttl_sec=30.0)

    assert record.phase == "restore_armed"
    assert maintenance_gate.maintenance_restore_standby_permit() is True
    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is False


def test_restore待命只在marker最终删除后交接写阶段(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """待命循环绝不能因 armed/claimed 本身放开任何写阶段。"""
    from codev_platform.reindex import maintenance_gate

    states = iter([True, True, False])
    waits: list[float] = []
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: next(states))
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_restore_standby_permit",
        lambda: True,
    )

    assert (
        maintenance_gate.wait_for_restore_standby_release(
            poll_sec=0.25,
            sleeper=waits.append,
        )
        is True
    )
    assert waits == [0.25, 0.25]


def test_restore待命检查期间marker正常删除也必须交接成功(monkeypatch) -> None:
    """complete 与待命轮询交错时，marker 消失不是身份失败。"""
    from codev_platform.reindex import maintenance_gate

    states = iter([True, False])
    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: next(states))
    monkeypatch.setattr(
        maintenance_gate,
        "maintenance_restore_standby_permit",
        lambda: False,
    )

    assert maintenance_gate.wait_for_restore_standby_release() is True


def test_维护门禁启用时非受控进程拒绝准入(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
    cgroup_path = tmp_path / "cgroup"
    cgroup_path.write_text("0::/user.slice\n", encoding="utf-8")
    _bind_cgroup_proof(monkeypatch, cgroup_path)

    assert maintenance_gate.maintenance_gate_allows_current_worker() is False


def test_维护门禁启用时cgroup证明异常拒绝准入(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)
    cgroup_path = tmp_path / "cgroup"
    cgroup_path.write_text("malformed-cgroup-line\n", encoding="utf-8")
    _bind_cgroup_proof(monkeypatch, cgroup_path)

    assert maintenance_gate.maintenance_gate_allows_current_worker() is False


def test_维护门禁启用时cgroup读取异常拒绝准入(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import external_worker_guard, maintenance_gate

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", lambda: True)

    def fail_cgroup_probe() -> bool:
        raise OSError("模拟 /proc 读取失败")

    monkeypatch.setattr(
        external_worker_guard,
        "current_process_in_reindex_unit_cgroup",
        fail_cgroup_probe,
    )

    assert maintenance_gate.maintenance_gate_allows_current_worker() is False


def test_当前进程准入在marker查询异常时拒绝(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    def fail_marker_probe() -> bool:
        raise OSError("模拟 marker 元数据读取失败")

    monkeypatch.setattr(maintenance_gate, "maintenance_gate_active", fail_marker_probe)

    assert maintenance_gate.maintenance_gate_allows_current_worker() is False


def test_非Linux默认全操作许可保持允许(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    monkeypatch.setattr(maintenance_gate, "_is_linux", lambda: False)

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux默认provision幂等且clean_deactivate预置锁(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = _bind_linux_global_gate(monkeypatch, tmp_path)

    maintenance_gate.provision_maintenance_gate()
    maintenance_gate.provision_maintenance_gate()
    maintenance_gate.deactivate_maintenance_gate()

    lock = marker.with_name(f".{marker.name}.lock")
    intent_lock = marker.with_name(f".{marker.name}.transition-intent.lock")
    session_lock = marker.with_name(f".{marker.name}.transition-session.lock")
    assert marker.exists() is False
    assert stat.S_ISREG(lock.stat().st_mode)
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644
    assert stat.S_ISREG(intent_lock.stat().st_mode)
    assert stat.S_IMODE(intent_lock.stat().st_mode) == 0o644
    assert stat.S_ISREG(session_lock.stat().st_mode)
    assert stat.S_IMODE(session_lock.stat().st_mode) == 0o644
    assert stat.S_IMODE(marker.parent.stat().st_mode) == 0o755


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
@pytest.mark.parametrize(
    "unsafe_kind",
    [
        "directory-link",
        "lock-link",
        "lock-mode",
        "intent-link",
        "intent-mode",
        "session-link",
        "session-mode",
    ],
)
def test_linux默认provision拒绝不安全路径状态(
    unsafe_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = _bind_linux_global_gate(monkeypatch, tmp_path)
    parent = marker.parent
    lock = marker.with_name(f".{marker.name}.lock")
    intent_lock = marker.with_name(f".{marker.name}.transition-intent.lock")
    session_lock = marker.with_name(f".{marker.name}.transition-session.lock")
    if unsafe_kind == "directory-link":
        target = tmp_path / "link-target"
        target.mkdir()
        parent.symlink_to(target, target_is_directory=True)
    else:
        parent.mkdir()
        parent.chmod(0o755)
        if unsafe_kind in {"lock-link", "intent-link", "session-link"}:
            target = tmp_path / "lock-target"
            target.write_bytes(b"not-a-lock")
            unsafe_lock = {
                "lock-link": lock,
                "intent-link": intent_lock,
                "session-link": session_lock,
            }[unsafe_kind]
            unsafe_lock.symlink_to(target)
        else:
            unsafe_lock = {
                "lock-mode": lock,
                "intent-mode": intent_lock,
                "session-mode": session_lock,
            }[unsafe_kind]
            unsafe_lock.write_bytes(b"not-a-lock")
            unsafe_lock.chmod(0o666)

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.provision_maintenance_gate()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux默认provision拒绝非预期所有者(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        maintenance_gate,
        "_global_owner_uid",
        lambda: os.geteuid() + 1,
    )
    monkeypatch.setattr(
        maintenance_gate,
        "_effective_uid",
        lambda: os.geteuid() + 1,
    )

    with pytest.raises(maintenance_gate.MaintenanceGateError):
        maintenance_gate.provision_maintenance_gate()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux全操作许可在缺失预置锁时拒绝(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        lambda: False,
    )

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux缺失转换意图锁时写许可保持关闭(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    marker = _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()
    marker.with_name(f".{marker.name}.transition-intent.lock").unlink()

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux转换意图阻止新读者插队并让转换先完成(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧读者排空期间，新读者必须停在 intent，不能继续抢 gate SH。"""
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()
    first_entered = threading.Event()
    release_first = threading.Event()
    writer_has_intent = threading.Event()
    writer_entered = threading.Event()
    release_writer = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []
    original_open_intent = maintenance_gate._open_global_transition_intent_lock

    def open_intent(*, exclusive: bool) -> int:
        descriptor = original_open_intent(exclusive=exclusive)
        if exclusive:
            writer_has_intent.set()
        return descriptor

    monkeypatch.setattr(
        maintenance_gate,
        "_open_global_transition_intent_lock",
        open_intent,
    )

    def first_reader() -> None:
        try:
            with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
                assert permitted is True
                first_entered.set()
                assert release_first.wait(2.0)
        except BaseException as error:
            errors.append(error)

    def writer() -> None:
        try:
            with maintenance_gate.maintenance_systemd_transition_lock():
                writer_entered.set()
                assert second_entered.is_set() is False
                assert release_writer.wait(2.0)
        except BaseException as error:
            errors.append(error)

    def second_reader() -> None:
        try:
            with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
                assert permitted is True
                second_entered.set()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=first_reader)
    transition = threading.Thread(target=writer)
    second = threading.Thread(target=second_reader)
    first.start()
    assert first_entered.wait(1.0)
    transition.start()
    assert writer_has_intent.wait(1.0)
    second.start()
    time.sleep(0.1)
    assert writer_entered.is_set() is False
    assert second_entered.is_set() is False

    release_first.set()
    assert writer_entered.wait(1.0)
    assert second_entered.is_set() is False
    release_writer.set()
    assert second_entered.wait(1.0)

    for thread in (first, transition, second):
        thread.join(2.0)
        assert thread.is_alive() is False
    assert errors == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux管理员会话锁不阻断worker的intent_gate共享准入(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """会话锁只串行管理员，冻结 worker 仍可在 marker 内完成待命身份读取。"""
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()
    monkeypatch.setattr(maintenance_gate, "_current_process_is_reindex_unit", lambda: False)

    with maintenance_gate.maintenance_systemd_transition_session():
        with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
            assert permitted is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux管理员会话锁不阻断冻结worker的待命身份许可(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冻结 worker 仍只取 intent/gate SH，长会话锁不得让它误判身份失败。"""
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    invocation_id = "a" * 32
    monkeypatch.setenv("INVOCATION_ID", invocation_id)
    monkeypatch.setattr(maintenance_gate, "_current_process_is_reindex_unit", lambda: True)
    maintenance_gate.activate_maintenance_gate()
    armed = maintenance_gate.arm_restore_standby(ttl_sec=30.0)
    maintenance_gate.claim_restore_standby(
        generation=armed.generation,
        invocation_id=invocation_id,
    )

    with maintenance_gate.maintenance_systemd_transition_session():
        assert maintenance_gate.maintenance_restore_standby_permit() is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux会话锁串行其他管理员转换但不阻断worker共享准入(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading
    import time

    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()
    session_entered = threading.Event()
    release_session = threading.Event()
    transition_entered = threading.Event()
    errors: list[BaseException] = []

    def hold_session() -> None:
        try:
            with maintenance_gate.maintenance_systemd_transition_session():
                session_entered.set()
                assert release_session.wait(2.0)
        except BaseException as error:
            errors.append(error)

    def enter_transition() -> None:
        try:
            with maintenance_gate.maintenance_systemd_transition_lock():
                transition_entered.set()
        except BaseException as error:
            errors.append(error)

    holder = threading.Thread(target=hold_session)
    waiter = threading.Thread(target=enter_transition)
    holder.start()
    assert session_entered.wait(1.0)
    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is True
    waiter.start()
    time.sleep(0.1)
    assert transition_entered.is_set() is False
    release_session.set()

    for thread in (holder, waiter):
        thread.join(2.0)
        assert thread.is_alive() is False
    assert transition_entered.is_set() is True
    assert errors == []


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux全操作许可在活跃marker时拒绝非受控进程(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.activate_maintenance_gate()
    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        lambda: False,
    )

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux无marker全操作许可不依赖cgroup证明(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()

    def fail_cgroup_probe() -> bool:
        raise OSError("模拟 /proc 读取失败")

    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        fail_cgroup_probe,
    )

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux全操作许可持有共享锁直至操作结束(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.provision_maintenance_gate()
    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        lambda: False,
    )
    entered = threading.Event()
    completed = threading.Event()

    def activate_gate() -> None:
        entered.set()
        maintenance_gate.activate_maintenance_gate()
        completed.set()

    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is True
        thread = threading.Thread(target=activate_gate)
        thread.start()
        assert entered.wait(1.0)
        time.sleep(0.1)
        assert completed.is_set() is False
    thread.join(2.0)

    assert thread.is_alive() is False
    assert completed.is_set() is True
    assert maintenance_gate.maintenance_gate_active() is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="仅 Linux 使用默认全局维护锁")
def test_linux受控service也不得取得通用写许可(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codev_platform.reindex import maintenance_gate

    _bind_linux_global_gate(monkeypatch, tmp_path)
    maintenance_gate.activate_maintenance_gate()
    monkeypatch.setattr(
        maintenance_gate,
        "_current_process_is_reindex_unit",
        lambda: True,
    )
    with maintenance_gate.maintenance_reindex_operation_permit() as permitted:
        assert permitted is False
    assert maintenance_gate.maintenance_gate_active() is True
