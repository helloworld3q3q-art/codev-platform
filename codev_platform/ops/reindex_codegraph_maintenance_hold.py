"""CodeGraph 跨重启维护门禁的可信文件叶子。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_HOLD_PATH,
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


CODEGRAPH_MAINTENANCE_HOLD_CONTENT = b"codev-platform-codegraph-maintenance-v1\n"
_HOLD_SPEC = SystemdMaintenanceHoldSpec(
    path=CODEGRAPH_MAINTENANCE_HOLD_PATH,
    content=CODEGRAPH_MAINTENANCE_HOLD_CONTENT,
)

SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[..., None]
FileRemover = Callable[[Path], bool]


class CodegraphMaintenanceHoldError(RuntimeError):
    """CodeGraph 耐久门禁无法建立、证明或解除。"""


def activate_codegraph_maintenance_hold(
    *,
    writer: FileWriter = write_root_owned_regular_file_atomic,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """耐久写入固定门禁并立即从可信 dirfd 链复证。"""
    try:
        activate_systemd_maintenance_hold(_HOLD_SPEC, writer=writer, reader=reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise CodegraphMaintenanceHoldError("CodeGraph 耐久维护门禁无法启用") from error


def verify_codegraph_maintenance_hold(
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """只接受固定内容、0644、root:root 的普通文件原像。"""
    try:
        verify_systemd_maintenance_hold(_HOLD_SPEC, reader=reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise CodegraphMaintenanceHoldError("CodeGraph 耐久维护门禁不受信任") from error


def deactivate_codegraph_maintenance_hold(
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    remover: FileRemover = remove_root_owned_regular_file,
) -> None:
    """先证明固定原像，耐久删除后再证明门禁确已缺失。"""
    try:
        deactivate_systemd_maintenance_hold(_HOLD_SPEC, reader=reader, remover=remover)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError as error:
        raise CodegraphMaintenanceHoldError("CodeGraph 耐久维护门禁无法解除") from error


__all__ = [
    "CODEGRAPH_MAINTENANCE_HOLD_CONTENT",
    "CODEGRAPH_MAINTENANCE_HOLD_PATH",
    "CodegraphMaintenanceHoldError",
    "activate_codegraph_maintenance_hold",
    "deactivate_codegraph_maintenance_hold",
    "verify_codegraph_maintenance_hold",
]
