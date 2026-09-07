"""systemd 安装事务的受信文件系统适配器。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallTransactionError,
    SystemdUnitFileSnapshot,
)
from codev_platform.mcp_systemd_unit_registry import (
    CANONICAL_SYSTEMD_UNIT_DIRECTORY,
    LEGACY_SYSTEMD_UNIT_DIRECTORY,
    managed_systemd_unit,
)


_LEGACY_SYSTEMD_UNIT_DIRECTORY = Path(LEGACY_SYSTEMD_UNIT_DIRECTORY)
_CANONICAL_SYSTEMD_UNIT_DIRECTORY = Path(CANONICAL_SYSTEMD_UNIT_DIRECTORY)
_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_MAX_UNIT_BYTES = 128 * 1024
_MAX_DROP_IN_BYTES = 128 * 1024


class _RootOwnedSnapshot(Protocol):
    """受信路径原像的最小只读端口。"""

    content: bytes
    mode: int
    uid: int
    gid: int


def default_unit_snapshot_reader(name: str) -> SystemdUnitFileSnapshot | None:
    """从 root 受信目录读取完整原像，回滚时保留 mode/uid/gid。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        _require_no_legacy_shadow(name)
        snapshot = read_optional_root_owned_regular_file_snapshot(
            _installed_unit_path(name),
            max_bytes=_MAX_UNIT_BYTES,
        )
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("systemd unit 原像不受信任") from error
    if snapshot is None:
        return None
    return _to_unit_snapshot(snapshot)


def default_drop_in_snapshot_reader(
    path: PurePosixPath,
) -> SystemdUnitFileSnapshot | None:
    """从有效路径读取 drop-in 完整 root 原像，不接受路径式跟随。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            Path(path.as_posix()),
            max_bytes=_MAX_DROP_IN_BYTES,
        )
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("systemd drop-in 原像不受信任") from error
    if snapshot is None:
        return None
    return _to_unit_snapshot(snapshot)


def _to_unit_snapshot(snapshot: _RootOwnedSnapshot) -> SystemdUnitFileSnapshot:
    """在受信路径层与安装事务层之间转换不可变原像。"""
    return SystemdUnitFileSnapshot(
        content=snapshot.content,
        mode=snapshot.mode,
        uid=snapshot.uid,
        gid=snapshot.gid,
    )


def default_unit_writer(name: str, content: bytes) -> None:
    """以固定属主和权限原子写入受管 unit。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        write_root_owned_regular_file_atomic,
    )

    try:
        _require_no_legacy_shadow(name)
        write_root_owned_regular_file_atomic(
            _installed_unit_path(name),
            content,
            mode=0o644,
            uid=0,
            gid=0,
        )
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("systemd unit 无法安全写入") from error


def default_unit_restorer(name: str, snapshot: SystemdUnitFileSnapshot | None) -> None:
    """按完整原像恢复 unit；安装前不存在时执行受信删除。"""
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        remove_root_owned_regular_file,
        write_root_owned_regular_file_atomic,
    )

    path = _installed_unit_path(name)
    try:
        _require_no_legacy_shadow(name)
        if snapshot is None:
            remove_root_owned_regular_file(path)
            return
        write_root_owned_regular_file_atomic(
            path,
            snapshot.content,
            mode=snapshot.mode,
            uid=snapshot.uid,
            gid=snapshot.gid,
        )
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("systemd unit 原像无法恢复") from error


def _installed_unit_path(name: str) -> Path:
    """只按受管注册表解析安装路径，拒绝调用方另造目录真值。"""
    try:
        registration = managed_systemd_unit(name)
    except ValueError as error:
        raise SystemdInstallTransactionError("systemd unit 不在受管注册表中") from error
    directory = (
        _CANONICAL_SYSTEMD_UNIT_DIRECTORY
        if registration.destination_directory == CANONICAL_SYSTEMD_UNIT_DIRECTORY
        else _LEGACY_SYSTEMD_UNIT_DIRECTORY
    )
    return directory / registration.name


def _require_no_legacy_shadow(name: str) -> None:
    """拒绝被 legacy 主文件遮蔽的 CodeGraph 写入，强制先走窄迁移事务。"""
    if name != _CODEGRAPH_UNIT:
        return
    from codev_platform.ops.reindex_codegraph_resume_managed_path import (
        TrustedManagedPathError,
        read_optional_root_owned_regular_file_snapshot,
    )

    try:
        snapshot = read_optional_root_owned_regular_file_snapshot(
            _LEGACY_SYSTEMD_UNIT_DIRECTORY / name,
            max_bytes=_MAX_UNIT_BYTES,
        )
    except TrustedManagedPathError as error:
        raise SystemdInstallTransactionError("CodeGraph legacy 主 unit 原像不受信任") from error
    if snapshot is not None:
        raise SystemdInstallTransactionError(
            "CodeGraph legacy 主 unit 会遮蔽 canonical 目录；请先执行布局迁移"
        )


__all__ = [
    "default_drop_in_snapshot_reader",
    "default_unit_restorer",
    "default_unit_snapshot_reader",
    "default_unit_writer",
]
