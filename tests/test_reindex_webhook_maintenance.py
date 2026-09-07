"""Webhook maintenance 生命周期的关闭、恢复和失败补偿回归。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _ok() -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_准备阶段先建立guard和hold再停止Webhook并完成全部证明() -> None:
    from codev_platform.ops.reindex_webhook_maintenance import (
        prepare_webhook_maintenance,
    )

    events: list[object] = []

    prepare_webhook_maintenance(
        platform_name="linux",
        command_runner=lambda command, **_kwargs: events.append(command) or _ok(),
        guard_ensurer=lambda: events.append("guard-on"),
        hold_activator=lambda: events.append("hold-on"),
        guard_proof=lambda: events.append("guard-proof"),
        hold_proof=lambda: events.append("hold-proof"),
        stopped_proof=lambda: events.append("stopped-proof"),
    )

    assert events == [
        "guard-on",
        "hold-on",
        ("systemctl", "stop", "codev-webhook.service"),
        ("systemctl", "reset-failed", "codev-webhook.service"),
        "guard-proof",
        "hold-proof",
        "stopped-proof",
    ]


def test_最终恢复在解除hold后启用启动并完成稳定和健康验收() -> None:
    from codev_platform.ops.reindex_webhook_maintenance import (
        resume_webhook_maintenance,
    )

    events: list[str] = []

    resume_webhook_maintenance(
        platform_name="linux",
        command_runner=lambda _command, **_kwargs: _ok(),
        guard_proof=lambda: events.append("guard-proof"),
        hold_proof=lambda: events.append("hold-proof"),
        enable_service=lambda: events.append("enable"),
        hold_deactivator=lambda: events.append("hold-off"),
        start_service=lambda: events.append("start"),
        running_proof=lambda: events.append("running"),
        health_proof=lambda: events.append("health"),
        settle_maintenance=lambda: events.append("settle"),
    )

    assert events == [
        "guard-proof",
        "hold-proof",
        "enable",
        "hold-off",
        "start",
        "running",
        "health",
    ]


def test_入口健康失败时重新收敛hold而不宣称恢复成功() -> None:
    from codev_platform.ops.reindex_webhook_maintenance import (
        WebhookMaintenanceLifecycleError,
        resume_webhook_maintenance,
    )

    events: list[str] = []

    with pytest.raises(WebhookMaintenanceLifecycleError, match="已回到入口维护态"):
        resume_webhook_maintenance(
            platform_name="linux",
            command_runner=lambda _command, **_kwargs: _ok(),
            guard_proof=lambda: events.append("guard-proof"),
            hold_proof=lambda: events.append("hold-proof"),
            enable_service=lambda: events.append("enable"),
            hold_deactivator=lambda: events.append("hold-off"),
            start_service=lambda: events.append("start"),
            running_proof=lambda: events.append("running"),
            health_proof=lambda: (_ for _ in ()).throw(RuntimeError("health")),
            settle_maintenance=lambda: events.append("settle"),
        )

    assert events == [
        "guard-proof",
        "hold-proof",
        "enable",
        "hold-off",
        "start",
        "running",
        "settle",
    ]
