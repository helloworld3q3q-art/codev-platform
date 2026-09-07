"""systemd 主 unit 布局迁移的 Linux 条件文件变更适配器。"""
from __future__ import annotations

import os
from pathlib import Path

from codev_platform._runtime_renameat2 import (
    rename_noreplace_at as _runtime_rename_noreplace_at,
    supports_rename_noreplace as _supports_rename_noreplace,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    TrustedManagedPathError,
    _close_quietly,
    _fchmod,
    _fchown,
    _fstat,
    _fsync,
    _open_optional_regular_file,
    _open_temporary_file,
    _open_trusted_parent,
    _read_bounded,
    _read_file_flags,
    _require_write_arguments,
    _root_owned_regular_file_metadata,
    _supports_dir_fd,
    _temporary_file_flags,
    _unlink_at,
    _unlink_temporary_quietly,
    _write_all,
)


def create_root_owned_regular_file_atomic_if_absent(
    path: Path,
    content: bytes,
    *,
    mode: int,
    uid: int = 0,
    gid: int = 0,
) -> None:
    """以同目录 `linkat` 条件首建叶子，目标出现即失败且绝不覆盖。"""
    _require_write_arguments(content, mode, uid, gid)
    if not _supports_conditional_create():
        raise TrustedManagedPathError("受管路径无法安全条件创建")
    _temporary_file_flags()
    parent_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    try:
        parent_descriptor, leaf = _open_trusted_parent(path, create_missing=True)
        temporary_name, temporary_descriptor = _open_temporary_file(parent_descriptor, mode)
        _write_all(temporary_descriptor, content)
        _fchown(temporary_descriptor, uid, gid)
        _fchmod(temporary_descriptor, mode)
        _fsync(temporary_descriptor)
        _link_at(temporary_name, leaf, parent_descriptor, parent_descriptor)
        _fsync(parent_descriptor)
        _unlink_at(temporary_name, parent_descriptor)
        temporary_name = None
        _fsync(parent_descriptor)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TrustedManagedPathError:
        raise
    except FileExistsError as error:
        raise TrustedManagedPathError("受管路径条件创建目标已存在") from error
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全条件创建") from error
    finally:
        if temporary_descriptor is not None:
            _close_quietly(temporary_descriptor)
        if temporary_name is not None and parent_descriptor is not None:
            _unlink_temporary_quietly(temporary_name, parent_descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)


def move_root_owned_regular_file_if_snapshot(
    source: Path,
    destination: Path,
    expected: RootOwnedRegularFileSnapshot,
) -> None:
    """条件移动同目录叶子；不遵守共同锁的 root 写者不在此 API 的隔离范围内。

    Linux 没有 compare-and-unlink。这里先用 `renameat2(RENAME_NOREPLACE)` 将源叶子
    原子移入非存在的隔离名，再复核 inode 与完整原像；发现移动前替换时，只尝试
    无覆盖地移回，绝不按原路径删除任何新叶子。
    """
    if not isinstance(expected, RootOwnedRegularFileSnapshot):
        raise TrustedManagedPathError("受管路径移动原像无效")
    if source.parent != destination.parent or source.name == destination.name:
        raise TrustedManagedPathError("受管路径条件移动必须在同一父目录")
    if not _supports_conditional_move():
        raise TrustedManagedPathError("受管路径无法安全条件移动")
    parent = _open_trusted_parent(source, create_missing=False)
    if parent is None:
        raise TrustedManagedPathError("受管路径原像已变化")
    parent_descriptor, source_leaf = parent
    try:
        current = _read_snapshot_at(
            source_leaf,
            parent_descriptor,
            max_bytes=max(len(expected.content), 1),
        )
        if current is None or not current.matches(expected, require_identity=True):
            raise TrustedManagedPathError("受管路径原像已变化")
        _rename_noreplace_at(source_leaf, destination.name, parent_descriptor)
        _fsync(parent_descriptor)
        moved = _read_snapshot_at(
            destination.name,
            parent_descriptor,
            max_bytes=max(len(expected.content), 1),
        )
        if moved is not None and moved.matches(expected, require_identity=True):
            return
        try:
            _rename_noreplace_at(destination.name, source_leaf, parent_descriptor)
            _fsync(parent_descriptor)
        except OSError as restore_error:
            raise TrustedManagedPathError("受管路径条件移动恢复失败") from restore_error
        raise TrustedManagedPathError("受管路径在条件移动前已变化")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TrustedManagedPathError:
        raise
    except FileExistsError as error:
        raise TrustedManagedPathError("受管路径条件移动目标已存在") from error
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全条件移动") from error
    finally:
        _close_quietly(parent_descriptor)


def _read_snapshot_at(
    leaf: str,
    parent_descriptor: int,
    *,
    max_bytes: int,
) -> RootOwnedRegularFileSnapshot | None:
    descriptor: int | None = None
    try:
        descriptor = _open_optional_regular_file(leaf, parent_descriptor, _read_file_flags())
        if descriptor is None:
            return None
        mode, uid, gid, device, inode = _root_owned_regular_file_metadata(_fstat(descriptor))
        return RootOwnedRegularFileSnapshot(
            content=_read_bounded(descriptor, max_bytes),
            mode=mode,
            uid=uid,
            gid=gid,
            device=device,
            inode=inode,
        )
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _supports_conditional_create() -> bool:
    return _supports_conditional_create_io() and _supports_dir_fd(os.link)


def _supports_conditional_create_io() -> bool:
    return (
        all(_supports_dir_fd(item) for item in (os.open, os.mkdir, os.unlink))
        and callable(getattr(os, "fchown", None))
        and callable(getattr(os, "fchmod", None))
    )


def _supports_conditional_move() -> bool:
    return _supports_rename_noreplace()


def _link_at(source: str, destination: str, source_parent: int, destination_parent: int) -> None:
    os.link(
        source,
        destination,
        src_dir_fd=source_parent,
        dst_dir_fd=destination_parent,
        follow_symlinks=False,
    )


def _rename_noreplace_at(source: str, destination: str, parent_descriptor: int) -> None:
    """调用 Linux `renameat2(RENAME_NOREPLACE)`，不允许退化为覆盖式 rename。"""
    _runtime_rename_noreplace_at(
        source,
        destination,
        parent_descriptor,
        parent_descriptor,
    )


__all__ = [
    "create_root_owned_regular_file_atomic_if_absent",
    "move_root_owned_regular_file_if_snapshot",
]
