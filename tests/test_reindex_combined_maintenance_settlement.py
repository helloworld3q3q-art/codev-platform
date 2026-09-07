"""组合激活在已持转换锁内失败关闭的回归测试。"""

from __future__ import annotations

import pytest

from tests.reindex_maintenance_test_support import (
    _isolate_legacy_webhook_boundary as _configure_legacy_webhook_boundary,
)


@pytest.fixture(autouse=True)
def _isolate_legacy_webhook_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_legacy_webhook_boundary(monkeypatch)


def test_锁内收敛按marker_CodeGraph生命周期_双服务_最终证明固定顺序执行() -> None:
    from codev_platform.ops.reindex_combined_maintenance_settlement import (
        settle_combined_maintenance_while_transition_locked,
    )

    events: list[str] = []

    def action(name: str, *, fail: bool = False):
        def run() -> None:
            events.append(name)
            if fail:
                raise RuntimeError(name)

        return run

    def prepare_codegraph() -> None:
        events.extend(("guard", "guard-proof", "hold", "codegraph"))
        raise RuntimeError("codegraph")

    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    with maintenance_systemd_transition_lock():
        result = settle_combined_maintenance_while_transition_locked(
            activate_reindex_gate_locked=action("marker"),
            prepare_codegraph=prepare_codegraph,
            restore_reindex_safety=action("dropin"),
            stop_reindex=action("stop"),
            prove_external_workers=action("prove-external"),
            prove_reindex_stopped=action("prove-reindex"),
            prove_codegraph_held=action("prove-codegraph"),
        )

    assert result.proven is False
    assert events == [
        "marker",
        "guard",
        "guard-proof",
        "hold",
        "codegraph",
        "dropin",
        "stop",
        "prove-external",
        "prove-reindex",
        "prove-codegraph",
    ]


def test_锁内收敛全部动作和证明成功才允许报告安全() -> None:
    from codev_platform.ops.reindex_combined_maintenance_settlement import (
        settle_combined_maintenance_while_transition_locked,
    )

    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    with maintenance_systemd_transition_lock():
        result = settle_combined_maintenance_while_transition_locked(
            activate_reindex_gate_locked=lambda: None,
            prepare_codegraph=lambda: None,
            restore_reindex_safety=lambda: None,
            stop_reindex=lambda: None,
            prove_external_workers=lambda: None,
            prove_reindex_stopped=lambda: None,
            prove_codegraph_held=lambda: None,
        )

    assert result.proven is True


def test_注入全部端口也不能绕过转换守卫() -> None:
    from codev_platform.ops.reindex_combined_maintenance_settlement import (
        settle_combined_maintenance_while_transition_locked,
    )
    from codev_platform.reindex.maintenance_gate import MaintenanceGateError

    with pytest.raises(MaintenanceGateError, match="转换守卫"):
        settle_combined_maintenance_while_transition_locked(
            activate_reindex_gate_locked=lambda: None,
            prepare_codegraph=lambda: None,
            restore_reindex_safety=lambda: None,
            stop_reindex=lambda: None,
            prove_external_workers=lambda: None,
            prove_reindex_stopped=lambda: None,
            prove_codegraph_held=lambda: None,
        )


def test_补偿遇到终止异常仍耗尽全部动作后再原样抛出() -> None:
    from codev_platform.ops.reindex_combined_maintenance_settlement import (
        settle_combined_maintenance_while_transition_locked,
    )
    from codev_platform.reindex.maintenance_gate import maintenance_systemd_transition_lock

    events: list[str] = []

    def interrupt() -> None:
        events.append("marker")
        raise KeyboardInterrupt("人工中断")

    def action(name: str):
        return lambda: events.append(name)

    with pytest.raises(KeyboardInterrupt, match="人工中断"):
        with maintenance_systemd_transition_lock():
            settle_combined_maintenance_while_transition_locked(
                activate_reindex_gate_locked=interrupt,
                prepare_codegraph=action("codegraph"),
                restore_reindex_safety=action("dropin"),
                stop_reindex=action("stop"),
                prove_external_workers=action("prove-external"),
                prove_reindex_stopped=action("prove-reindex"),
                prove_codegraph_held=action("prove-codegraph"),
            )

    assert events == [
        "marker",
        "codegraph",
        "dropin",
        "stop",
        "prove-external",
        "prove-reindex",
        "prove-codegraph",
    ]
