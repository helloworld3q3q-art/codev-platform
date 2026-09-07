"""reindex 与 CodeGraph 跨重启 systemd 维护契约的单一真值。"""

from __future__ import annotations

from pathlib import Path


CODEGRAPH_MAINTENANCE_HOLD_PATH = Path("/var/lib/codev-platform/codegraph-maintenance.gate")
CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE = (
    f"ConditionPathExists=!{CODEGRAPH_MAINTENANCE_HOLD_PATH.as_posix()}"
)
CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH = Path(
    "/etc/systemd/system/codev-mcp-codegraph.service.d/99-codev-maintenance-condition.conf"
)
CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT = (
    f"[Unit]\n{CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE}\n".encode("ascii")
)
WEBHOOK_MAINTENANCE_HOLD_PATH = Path("/var/lib/codev-platform/webhook-maintenance.gate")
WEBHOOK_MAINTENANCE_CONDITION_DIRECTIVE = (
    f"ConditionPathExists=!{WEBHOOK_MAINTENANCE_HOLD_PATH.as_posix()}"
)
WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH = Path(
    "/etc/systemd/system/codev-webhook.service.d/99-codev-maintenance-condition.conf"
)
WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT = (
    f"[Unit]\n{WEBHOOK_MAINTENANCE_CONDITION_DIRECTIVE}\n".encode("ascii")
)


__all__ = [
    "CODEGRAPH_MAINTENANCE_CONDITION_DIRECTIVE",
    "CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT",
    "CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH",
    "CODEGRAPH_MAINTENANCE_HOLD_PATH",
    "WEBHOOK_MAINTENANCE_CONDITION_DIRECTIVE",
    "WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT",
    "WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH",
    "WEBHOOK_MAINTENANCE_HOLD_PATH",
]
