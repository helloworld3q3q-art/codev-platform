"""reindex 恢复待命与最终交接拆分的状态机测试。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest


def _ports(module, events: list[str]):
    invocation = "b" * 32

    def stage(name: str, value=None):
        def run(*_args):
            events.append(name)
            return value

        return run

    return module.ReindexRestoreHandoffPorts(
        prove_dropin=stage("dropin"),
        prove_gate_active=stage("gate"),
        prove_reindex_stopped=stage("stopped"),
        prove_external_workers=stage("external"),
        arm_standby=stage("arm", SimpleNamespace(generation="a" * 32)),
        start_reindex=stage("start"),
        read_invocation=stage("identity", invocation),
        claim_standby=stage("claim"),
        renew_standby=stage("renew"),
        prove_stable=stage("stable"),
        enable_reindex=stage("enable"),
        restore_baseline_restart=stage("restart-always"),
        renew_standby_locked=stage("renew-locked"),
        complete_standby_locked=stage("complete"),
    )


def _within_transition(action):
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    with maintenance_systemd_transition_lock():
        return action()


def _within_session(action):
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_session

    with maintenance_systemd_transition_session():
        return action()


def test_待命准备只绑定稳定实例且不删除marker或恢复Restart() -> None:
    from codev_platform.ops import reindex_restore_handoff as module

    events: list[str] = []
    handoff = _within_session(
        lambda: module.prepare_reindex_restore_handoff(ports=_ports(module, events))
    )

    assert handoff == module.ReindexRestoreHandoff("a" * 32, "b" * 32)
    assert events == [
        "dropin",
        "gate",
        "stopped",
        "external",
        "arm",
        "start",
        "identity",
        "claim",
        "renew",
        "stable",
        "renew",
        "dropin",
        "identity",
    ]
    assert "complete" not in events
    assert "restart-always" not in events


def test_会话内结算先enable再恢复Restart且仍不删除marker() -> None:
    from codev_platform.ops import reindex_restore_handoff as module

    events: list[str] = []
    ports = _ports(module, events)
    handoff = module.ReindexRestoreHandoff("a" * 32, "b" * 32)

    _within_session(
        lambda: module.settle_reindex_restore_handoff_while_session_locked(
            handoff,
            ports=ports,
        )
    )

    assert events == [
        "dropin",
        "identity",
        "renew",
        "enable",
        "restart-always",
        "stable",
        "renew",
        "identity",
    ]
    assert "complete" not in events


def test_最终短锁交接只复证InvocationID而不等待稳定窗口() -> None:
    from codev_platform.ops import reindex_restore_handoff as module

    events: list[str] = []
    _within_transition(
        lambda: module.complete_reindex_restore_handoff_while_transition_locked(
            module.ReindexRestoreHandoff("a" * 32, "b" * 32),
            ports=_ports(module, events),
        )
    )

    assert events == [
        "renew-locked",
        "identity",
        "complete",
    ]


def test_最终交接删除前InvocationID漂移时不得删除marker() -> None:
    from codev_platform.ops import reindex_restore_handoff as module
    from codev_platform.ops.reindex_maintenance import ReindexMaintenanceError

    events: list[str] = []
    ports = replace(
        _ports(module, events),
        read_invocation=lambda: "c" * 32,
    )

    with pytest.raises(ReindexMaintenanceError, match="最终交接失败") as captured:
        _within_transition(
            lambda: module.complete_reindex_restore_handoff_while_transition_locked(
                module.ReindexRestoreHandoff("a" * 32, "b" * 32),
                ports=ports,
            )
        )

    assert "已变化" in str(captured.value.__cause__)
    assert events == ["renew-locked"]


def test_最终交接删除marker后不再执行任何可能失败的外部动作() -> None:
    from codev_platform.ops import reindex_restore_handoff as module

    events: list[str] = []
    identities = iter(("b" * 32,))
    ports = replace(
        _ports(module, events),
        read_invocation=lambda: events.append("identity") or next(identities),
    )

    _within_transition(
        lambda: module.complete_reindex_restore_handoff_while_transition_locked(
            module.ReindexRestoreHandoff("a" * 32, "b" * 32),
            ports=ports,
        )
    )

    assert events == [
        "renew-locked",
        "identity",
        "complete",
    ]


def test_注入ports也不能绕过转换守卫() -> None:
    from codev_platform.ops import reindex_restore_handoff as module
    from codev_platform.reindex.maintenance_gate import MaintenanceGateError

    with pytest.raises(MaintenanceGateError, match="转换守卫"):
        module.complete_reindex_restore_handoff_while_transition_locked(
            module.ReindexRestoreHandoff("a" * 32, "b" * 32),
            ports=_ports(module, []),
        )


def test_待命准备与会话结算不能绕过会话守卫() -> None:
    from codev_platform.ops import reindex_restore_handoff as module
    from codev_platform.reindex.maintenance_gate import MaintenanceGateError

    with pytest.raises(MaintenanceGateError, match="转换会话守卫"):
        module.prepare_reindex_restore_handoff(ports=_ports(module, []))

    with pytest.raises(MaintenanceGateError, match="转换会话守卫"):
        module.settle_reindex_restore_handoff_while_session_locked(
            module.ReindexRestoreHandoff("a" * 32, "b" * 32),
            ports=_ports(module, []),
        )


def test_最终短锁使用有界InvocationID读取() -> None:
    from codev_platform.ops import reindex_restore_handoff as module

    timeouts: list[float] = []

    def run(_command, *, timeout_sec: float):
        timeouts.append(timeout_sec)
        return SimpleNamespace(
            returncode=0,
            stdout=f"ActiveState=active\nSubState=running\nInvocationID={'b' * 32}\n",
        )

    assert module._default_invocation_reader(run)() == "b" * 32
    assert timeouts == [1.0]
