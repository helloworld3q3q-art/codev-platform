"""reindex 维护编排的 CodeGraph 转换临界区回归。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.reindex_maintenance_test_support import (
    _isolate_legacy_webhook_boundary as _configure_legacy_webhook_boundary,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_webhook_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_legacy_webhook_boundary(monkeypatch)


def _dropin(tmp_path: Path) -> Path:
    return tmp_path / "codev-reindex.service.d" / "10-codev-reindex-maintenance.conf"


def _ok(*, stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_默认marker在转换锁内写入失败时仍补偿但拒绝声称安全已证明(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """默认 marker 写失败不能跳过两个服务边界的最终收敛。"""
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        prepare_reindex_maintenance,
    )

    events: list[str] = []
    commands: list[tuple[str, ...]] = []

    @contextmanager
    def gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    @contextmanager
    def transition_intent():
        events.append("intent-enter")
        try:
            yield gate_lock
        finally:
            events.append("intent-exit")

    def fail_marker() -> None:
        events.append("marker")
        raise RuntimeError("marker write failed")

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", transition_intent)
    monkeypatch.setattr(
        prepare_module,
        "_default_activate_gate_while_transition_intent_locked",
        fail_marker,
    )

    with pytest.raises(ReindexMaintenanceError, match="安全状态未证明"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            external_worker_proof=lambda: events.append("external-proof"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert events == [
        "intent-enter",
        "marker",
        "intent-exit",
        "intent-enter",
        "marker",
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
        "intent-exit",
        "external-proof",
        "reindex-proof",
        "codegraph-proof",
    ]
    assert commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "codev-reindex.service"),
        ("systemctl", "reset-failed", "codev-reindex.service"),
    ]


def test_默认转换锁忙时预停worker后重试同锁CodeGraph与marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """长 reindex 持共享锁时，默认路径也必须先受控收敛再取得独占锁。"""
    from codev_platform.ops import reindex_maintenance as maintenance
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    attempts = 0
    events: list[str] = []
    commands: list[tuple[str, ...]] = []

    @contextmanager
    def acquired_gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    def gate_lock():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise MaintenanceGateLockBusyError("共享 worker 尚未退出")
        return acquired_gate_lock()

    @contextmanager
    def transition_intent():
        events.append("intent-enter")
        try:
            yield gate_lock
        finally:
            events.append("intent-exit")

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", transition_intent)
    monkeypatch.setattr(
        prepare_module,
        "_default_activate_gate_while_transition_intent_locked",
        lambda: events.append("marker"),
        raising=False,
    )
    monkeypatch.setattr(
        maintenance,
        "_restore_safety_dropin",
        lambda *_args: events.append("dropin"),
    )
    monkeypatch.setattr(
        maintenance,
        "_stop_reindex_safely",
        lambda *_args: events.append("preempt-stop"),
    )

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
        codegraph_prepare=lambda: events.append("codegraph-prepare"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        external_worker_proof=lambda: events.append("external-proof"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert attempts == 2
    assert events == [
        "intent-enter",
        "marker",
        "dropin",
        "preempt-stop",
        "external-proof",
        "reindex-proof",
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
        "intent-exit",
        "dropin",
        "external-proof",
        "reindex-proof",
        "codegraph-proof",
    ]
    assert commands.count(("systemctl", "stop", "codev-reindex.service")) == 1


def test_转换意图本身锁忙时不得预停reindex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """只有已经持有 intent 后的 gate 忙才允许预停，避免干扰其他管理员事务。"""
    from codev_platform.ops import reindex_maintenance as maintenance
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    events: list[str] = []

    @contextmanager
    def busy_intent():
        raise MaintenanceGateLockBusyError("已有 systemd 转换正在执行")
        yield

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", busy_intent)

    with pytest.raises(prepare_module._TransitionIntentEntryError) as captured:
        prepare_module._prepare_codegraph_with_bounded_preemption(
            maintenance=maintenance,
            path=_dropin(tmp_path),
            run=lambda command, **_kwargs: events.append(" ".join(command)) or _ok(),
            prove_reindex=lambda: events.append("reindex-proof"),
            prove_external=lambda: events.append("external-proof"),
            prepare_codegraph=lambda: events.append("codegraph-prepare"),
            activate_gate_while_intent_locked=lambda: events.append("marker"),
        )

    assert isinstance(captured.value.__cause__, MaintenanceGateLockBusyError)
    assert events == []


def test_公开prepare在intent入场失败时不执行任何补偿写入(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """尚未取得 intent 就失败代表零变更，不能越过另一管理员去 stop 服务。"""
    from codev_platform.ops import reindex_maintenance as maintenance
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.reindex.maintenance_gate import MaintenanceGateLockBusyError

    commands: list[tuple[str, ...]] = []
    events: list[str] = []

    @contextmanager
    def busy_intent():
        raise MaintenanceGateLockBusyError("已有 systemd 转换正在执行")
        yield

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", busy_intent)

    with pytest.raises(maintenance.ReindexMaintenanceError, match="运行态未更改"):
        maintenance.prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda command, **_kwargs: commands.append(command) or _ok(),
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            external_worker_proof=lambda: events.append("external-proof"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert commands == []
    assert events == []


def test_prepare补偿期中断仍耗尽双服务收敛后再抛出(tmp_path: Path) -> None:
    """Ctrl+C 不能截断 marker 之后的 stop、CodeGraph 和最终证明。"""
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []
    gate_attempts = 0

    def activate_gate() -> None:
        nonlocal gate_attempts
        gate_attempts += 1
        events.append(f"gate-{gate_attempts}")
        if gate_attempts == 1:
            raise RuntimeError("首次失败")
        raise KeyboardInterrupt("补偿期中断")

    with pytest.raises(KeyboardInterrupt, match="补偿期中断"):
        prepare_reindex_maintenance(
            platform_name="linux",
            dropin_path=_dropin(tmp_path),
            command_runner=lambda command, **_kwargs: events.append(" ".join(command)) or _ok(),
            gate_activator=activate_gate,
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
            external_worker_proof=lambda: events.append("external-proof"),
            stop_proof=lambda: events.append("reindex-proof"),
        )

    assert events[0:2] == ["gate-1", "gate-2"]
    assert "codegraph-prepare" in events
    assert events[-3:] == ["external-proof", "reindex-proof", "codegraph-proof"]


def test_restore补偿的CodeGraph收敛也必须持有同一转换锁(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """restore 后置失败不能绕过 installer、prepare 与迁移共用的转换锁。"""
    from codev_platform.ops import reindex_maintenance_restore as restore_module
    from codev_platform.ops.reindex_maintenance import (
        ReindexMaintenanceError,
        restore_reindex_maintenance,
    )

    events: list[str] = []

    @contextmanager
    def transition_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    monkeypatch.setattr(
        restore_module,
        "_codegraph_transition_lock",
        lambda: transition_lock(),
        raising=False,
    )
    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")

    with pytest.raises(ReindexMaintenanceError, match="已证明安全停机状态"):
        restore_reindex_maintenance(
            platform_name="linux",
            dropin_path=path,
            command_runner=lambda command, **_kwargs: _ok(
                stdout="always\n" if command[:2] == ("systemctl", "show") else ""
            ),
            stop_proof=lambda: events.append("reindex-proof"),
            external_worker_proof=lambda: events.append("external-proof"),
            stability_probe=lambda: events.append("stable"),
            gate_activator=lambda: events.append("marker-reset"),
            gate_active_reader=lambda: True,
            standby_armer=lambda: SimpleNamespace(generation="a" * 32),
            unit_invocation_reader=lambda: "b" * 32,
            standby_claimer=lambda *_args: events.append("claim"),
            standby_renewer=lambda *_args: events.append("renew"),
            standby_completer=lambda *_args: (_ for _ in ()).throw(RuntimeError("交接失败")),
            codegraph_prepare=lambda: events.append("codegraph-prepare"),
            codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        )

    start = events.index("codegraph-prepare")
    assert events[start - 1 : start + 2] == [
        "transition-enter",
        "codegraph-prepare",
        "transition-exit",
    ]
