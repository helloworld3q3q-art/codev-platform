"""reindex maintenance 与 Webhook 持久关闭边界的接线回归。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


def _dropin(tmp_path: Path) -> Path:
    return tmp_path / "codev-reindex.service.d" / "10-codev-reindex-maintenance.conf"


def _ok() -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_prepare先关闭Webhook再停止reindex并把入口证明纳入维护成功条件(
    tmp_path: Path,
) -> None:
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []

    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda _command, **_kwargs: _ok(),
        gate_activator=lambda: events.append("gate"),
        codegraph_prepare=lambda: events.append("codegraph-prepare"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        webhook_prepare=lambda: events.append("webhook-prepare"),
        webhook_maintenance_proof=lambda: events.append("webhook-proof"),
        external_worker_proof=lambda: events.append("external-proof"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert events == [
        "gate",
        "codegraph-prepare",
        "webhook-prepare",
        "external-proof",
        "reindex-proof",
        "codegraph-proof",
        "webhook-proof",
    ]


def test_status把Webhook关闭证明纳入同一维护状态结论(tmp_path: Path) -> None:
    from codev_platform.ops.reindex_maintenance import inspect_reindex_maintenance

    path = _dropin(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nRestart=no\n", encoding="utf-8")
    events: list[str] = []

    inspect_reindex_maintenance(
        platform_name="linux",
        dropin_path=path,
        gate_active_reader=lambda: True,
        gate_record_reader=lambda: SimpleNamespace(phase="maintenance"),
        external_worker_proof=lambda: events.append("external-proof"),
        stop_proof=lambda: events.append("reindex-proof"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        webhook_maintenance_proof=lambda: events.append("webhook-proof"),
    )

    assert events == [
        "external-proof",
        "reindex-proof",
        "codegraph-proof",
        "webhook-proof",
    ]


def test_prepare在同一转换锁内依次收敛CodeGraph和Webhook(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from codev_platform.ops import reindex_maintenance_prepare as prepare_module
    from codev_platform.ops.reindex_maintenance import prepare_reindex_maintenance

    events: list[str] = []

    @contextmanager
    def gate_lock():
        events.append("transition-enter")
        try:
            yield
        finally:
            events.append("transition-exit")

    @contextmanager
    def transition_intent():
        yield gate_lock

    monkeypatch.setattr(prepare_module, "_codegraph_transition_intent", transition_intent)
    prepare_reindex_maintenance(
        platform_name="linux",
        dropin_path=_dropin(tmp_path),
        command_runner=lambda _command, **_kwargs: _ok(),
        gate_activator=lambda: events.append("gate"),
        codegraph_prepare=lambda: events.append("codegraph-prepare"),
        codegraph_maintenance_proof=lambda: events.append("codegraph-proof"),
        webhook_prepare=lambda: events.append("webhook-prepare"),
        webhook_maintenance_proof=lambda: events.append("webhook-proof"),
        external_worker_proof=lambda: events.append("external-proof"),
        stop_proof=lambda: events.append("reindex-proof"),
    )

    assert events.index("transition-enter") < events.index("codegraph-prepare")
    assert events.index("codegraph-prepare") < events.index("webhook-prepare")
    assert events.index("webhook-prepare") < events.index("transition-exit")
