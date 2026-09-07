"""Webhook 跨重启维护 hold 的窄适配器。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from codev_platform.core.systemd_maintenance_contract import (
    WEBHOOK_MAINTENANCE_HOLD_PATH,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    read_optional_root_owned_regular_file_snapshot,
    remove_root_owned_regular_file,
    write_root_owned_regular_file_atomic,
)
from codev_platform.ops.systemd_maintenance_hold import (
    SystemdMaintenanceHoldError,
    SystemdMaintenanceHoldSpec,
    activate_systemd_maintenance_hold,
    deactivate_systemd_maintenance_hold,
    verify_systemd_maintenance_hold,
)


WEBHOOK_MAINTENANCE_HOLD_CONTENT = b"codev-platform-webhook-maintenance-v1\n"
_HOLD_SPEC = SystemdMaintenanceHoldSpec(
    path=WEBHOOK_MAINTENANCE_HOLD_PATH,
    content=WEBHOOK_MAINTENANCE_HOLD_CONTENT,
)

SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[..., None]
FileRemover = Callable[[Path], bool]


class WebhookMaintenanceHoldError(RuntimeError):
    """Webhook 耐久维护 hold 无法建立、证明或解除。"""


def activate_webhook_maintenance_hold(
    *,
    writer: FileWriter = write_root_owned_regular_file_atomic,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """耐久写入固定 Webhook hold 并复证可信原像。"""
    try:
        activate_systemd_maintenance_hold(_HOLD_SPEC, writer=writer, reader=reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise WebhookMaintenanceHoldError("Webhook 耐久维护 hold 无法启用") from error


def verify_webhook_maintenance_hold(
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """证明 Webhook hold 的内容、权限和所有者均为规范状态。"""
    try:
        verify_systemd_maintenance_hold(_HOLD_SPEC, reader=reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise WebhookMaintenanceHoldError("Webhook 耐久维护 hold 不受信任") from error


def deactivate_webhook_maintenance_hold(
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    remover: FileRemover = remove_root_owned_regular_file,
) -> None:
    """只在原像已证明时耐久删除 Webhook hold。"""
    try:
        deactivate_systemd_maintenance_hold(_HOLD_SPEC, reader=reader, remover=remover)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise WebhookMaintenanceHoldError("Webhook 耐久维护 hold 无法解除") from error


__all__ = [
    "WEBHOOK_MAINTENANCE_HOLD_CONTENT",
    "WEBHOOK_MAINTENANCE_HOLD_PATH",
    "WebhookMaintenanceHoldError",
    "activate_webhook_maintenance_hold",
    "deactivate_webhook_maintenance_hold",
    "verify_webhook_maintenance_hold",
]
