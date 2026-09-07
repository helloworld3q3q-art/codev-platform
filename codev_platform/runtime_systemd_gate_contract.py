"""部署总门禁与 Webhook 入口门禁的固定路径和 systemd 条件真值。"""

from __future__ import annotations

from pathlib import PurePosixPath

from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES


_SYSTEMD_ROOT = PurePosixPath("/etc/systemd/system")
DEPLOYMENT_GUARD_MARKER_PATH = PurePosixPath(
    "/var/lib/codev-platform/runtime/deployment-maintenance.guard"
)
INGRESS_GATE_MARKER_PATH = PurePosixPath(
    "/var/lib/codev-platform/runtime/ingress-closed.guard"
)
DEPLOYMENT_GUARD_DROP_IN_CONTENT = (
    b"[Unit]\nConditionPathExists=!"
    + DEPLOYMENT_GUARD_MARKER_PATH.as_posix().encode("ascii")
    + b"\n"
)
INGRESS_GATE_DROP_IN_CONTENT = (
    b"[Unit]\nConditionPathExists=!"
    + INGRESS_GATE_MARKER_PATH.as_posix().encode("ascii")
    + b"\n"
)
_DEPLOYMENT_DROP_IN_NAME = "10-codev-deployment-guard.conf"
_INGRESS_DROP_IN_NAME = "20-codev-ingress-gate.conf"
_WEBHOOK_UNIT = "codev-webhook.service"


def deployment_guard_drop_in_path(unit: str) -> PurePosixPath:
    """按固定受管 unit 生成部署总门禁 drop-in 路径。"""
    if type(unit) is not str or unit not in MANAGED_SYSTEMD_UNIT_NAMES:
        raise ValueError("部署门禁 unit 不受管")
    return _SYSTEMD_ROOT / f"{unit}.d" / _DEPLOYMENT_DROP_IN_NAME


def ingress_gate_drop_in_path() -> PurePosixPath:
    """返回唯一 Webhook 入口门禁 drop-in 路径。"""
    return _SYSTEMD_ROOT / f"{_WEBHOOK_UNIT}.d" / _INGRESS_DROP_IN_NAME


__all__ = [
    "DEPLOYMENT_GUARD_DROP_IN_CONTENT",
    "DEPLOYMENT_GUARD_MARKER_PATH",
    "INGRESS_GATE_DROP_IN_CONTENT",
    "INGRESS_GATE_MARKER_PATH",
    "deployment_guard_drop_in_path",
    "ingress_gate_drop_in_path",
]
