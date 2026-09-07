"""root-only systemd 维护 hold 的可信文件机械层。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    read_optional_root_owned_regular_file_snapshot,
    remove_root_owned_regular_file,
    write_root_owned_regular_file_atomic,
)


_HOLD_MODE = 0o644
_MAX_HOLD_BYTES = 256

SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[..., None]
FileRemover = Callable[[Path], bool]


class SystemdMaintenanceHoldError(RuntimeError):
    """固定 systemd 维护 hold 无法建立、证明或解除。"""


@dataclass(frozen=True, slots=True)
class SystemdMaintenanceHoldSpec:
    """一个固定 root-only hold 的路径和规范内容。"""

    path: Path
    content: bytes

    def __post_init__(self) -> None:
        try:
            path = PurePosixPath(Path(self.path).as_posix())
        except (TypeError, ValueError):
            raise SystemdMaintenanceHoldError("systemd 维护 hold 规格无效") from None
        content = self.content
        if (
            not path.is_absolute()
            or path.as_posix() != Path(self.path).as_posix()
            or ".." in path.parts
            or type(content) is not bytes
            or not content
            or len(content) > _MAX_HOLD_BYTES
            or not content.endswith(b"\n")
            or any(byte < 32 or byte > 126 for byte in content[:-1])
        ):
            raise SystemdMaintenanceHoldError("systemd 维护 hold 规格无效")


def activate_systemd_maintenance_hold(
    spec: SystemdMaintenanceHoldSpec,
    *,
    writer: FileWriter = write_root_owned_regular_file_atomic,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """耐久写入固定 hold 后立即按同一可信读取边界复证。"""
    checked = _require_spec(spec)
    _require_callables(writer, reader)
    try:
        writer(
            checked.path,
            checked.content,
            mode=_HOLD_MODE,
            uid=0,
            gid=0,
        )
        verify_systemd_maintenance_hold(checked, reader=reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdMaintenanceHoldError:
        raise
    except Exception as error:
        raise SystemdMaintenanceHoldError("systemd 维护 hold 无法启用") from error


def verify_systemd_maintenance_hold(
    spec: SystemdMaintenanceHoldSpec,
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
) -> None:
    """只接受规范内容、0644 和 root:root 的普通文件快照。"""
    checked = _require_spec(spec)
    _require_callables(reader)
    try:
        snapshot = reader(checked.path, max_bytes=_MAX_HOLD_BYTES)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdMaintenanceHoldError("systemd 维护 hold 不受信任") from error
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.content != checked.content
        or snapshot.mode != _HOLD_MODE
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise SystemdMaintenanceHoldError("systemd 维护 hold 不受信任")


def deactivate_systemd_maintenance_hold(
    spec: SystemdMaintenanceHoldSpec,
    *,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    remover: FileRemover = remove_root_owned_regular_file,
) -> None:
    """先复证当前 hold，再耐久删除并证明固定路径已经缺失。"""
    checked = _require_spec(spec)
    _require_callables(reader, remover)
    verify_systemd_maintenance_hold(checked, reader=reader)
    try:
        removed = remover(checked.path)
        remaining = reader(checked.path, max_bytes=_MAX_HOLD_BYTES)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise SystemdMaintenanceHoldError("systemd 维护 hold 无法解除") from error
    if removed is not True or remaining is not None:
        raise SystemdMaintenanceHoldError("systemd 维护 hold 无法解除")


def _require_spec(value: object) -> SystemdMaintenanceHoldSpec:
    if type(value) is not SystemdMaintenanceHoldSpec:
        raise SystemdMaintenanceHoldError("systemd 维护 hold 规格无效")
    return value


def _require_callables(*items: object) -> None:
    if not all(callable(item) for item in items):
        raise SystemdMaintenanceHoldError("systemd 维护 hold 适配器不可用")


__all__ = [
    "SystemdMaintenanceHoldError",
    "SystemdMaintenanceHoldSpec",
    "activate_systemd_maintenance_hold",
    "deactivate_systemd_maintenance_hold",
    "verify_systemd_maintenance_hold",
]
