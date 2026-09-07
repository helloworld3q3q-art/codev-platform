"""部署与入口 systemd 门禁固定真值测试。"""

from __future__ import annotations

import pytest

from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    DEPLOYMENT_GUARD_MARKER_PATH,
    INGRESS_GATE_DROP_IN_CONTENT,
    INGRESS_GATE_MARKER_PATH,
    deployment_guard_drop_in_path,
    ingress_gate_drop_in_path,
)


def test_部署与入口门禁路径和condition只有一个真值源() -> None:
    assert DEPLOYMENT_GUARD_MARKER_PATH.as_posix() == (
        "/var/lib/codev-platform/runtime/deployment-maintenance.guard"
    )
    assert DEPLOYMENT_GUARD_DROP_IN_CONTENT == (
        b"[Unit]\n"
        b"ConditionPathExists=!/var/lib/codev-platform/runtime/deployment-maintenance.guard\n"
    )
    assert deployment_guard_drop_in_path("codev-mcp-codegraph.service").as_posix() == (
        "/etc/systemd/system/codev-mcp-codegraph.service.d/10-codev-deployment-guard.conf"
    )
    assert INGRESS_GATE_MARKER_PATH.as_posix() == (
        "/var/lib/codev-platform/runtime/ingress-closed.guard"
    )
    assert INGRESS_GATE_DROP_IN_CONTENT == (
        b"[Unit]\n"
        b"ConditionPathExists=!/var/lib/codev-platform/runtime/ingress-closed.guard\n"
    )
    assert ingress_gate_drop_in_path().as_posix() == (
        "/etc/systemd/system/codev-webhook.service.d/20-codev-ingress-gate.conf"
    )


def test_部署门禁拒绝注册表外unit() -> None:
    with pytest.raises(ValueError, match="不受管"):
        deployment_guard_drop_in_path("unknown.service")
