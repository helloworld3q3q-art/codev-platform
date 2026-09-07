"""运行时父命名空间的权限收敛与只读复验。"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from codev_platform._runtime_service_access_contracts import (
    RuntimeServiceAccessError,
    RuntimeServiceNamespaceProof,
    _NamespaceEntry,
)

_NAMESPACE_NAMES = ("", "bases", "releases")
_NAMESPACE_MIGRATION_MODES = frozenset({0o700, 0o710, 0o750, 0o755})
_NAMESPACE_TARGET_MODE = 0o710
_NamespaceReference = Callable[
    [tuple[_NamespaceEntry, ...], _NamespaceEntry, Path],
    os.stat_result,
]


def _namespace_proof(service_uid: int, service_gid: int) -> RuntimeServiceNamespaceProof:
    return RuntimeServiceNamespaceProof(
        service_uid=service_uid,
        service_gid=service_gid,
        directory_count=len(_NAMESPACE_NAMES),
        mode=_NAMESPACE_TARGET_MODE,
    )


def _verify_namespace(
    root: Path,
    service_uid: int,
    service_gid: int,
) -> RuntimeServiceNamespaceProof:
    try:
        with ExitStack() as stack:
            entries = _open_namespace_entries(
                stack,
                root,
                target_gid=service_gid,
                require_target=True,
            )
            _verify_namespace_references(entries, root)
    except RuntimeServiceAccessError:
        raise
    except (OSError, TypeError, ValueError):
        raise RuntimeServiceAccessError("运行时服务命名空间复验失败") from None
    return _namespace_proof(service_uid, service_gid)


def _open_namespace_entries(
    stack: ExitStack,
    root: Path,
    *,
    target_gid: int,
    require_target: bool,
) -> tuple[_NamespaceEntry, ...]:
    try:
        linked_root = root.lstat()
        root_fd = _open_directory(root)
        stack.callback(os.close, root_fd)
        opened_root = os.fstat(root_fd)
        _require_same_namespace_entry(linked_root, opened_root)
        root_device = opened_root.st_dev
        entries = [
            _namespace_entry(
                "",
                root_fd,
                opened_root,
                root_device=root_device,
                target_gid=target_gid,
                require_target=require_target,
            )
        ]
        for name in _NAMESPACE_NAMES[1:]:
            linked = _stat_at(root_fd, name)
            descriptor = _open_directory(name, dir_fd=root_fd)
            stack.callback(os.close, descriptor)
            opened = os.fstat(descriptor)
            _require_same_namespace_entry(linked, opened)
            entries.append(
                _namespace_entry(
                    name,
                    descriptor,
                    opened,
                    root_device=root_device,
                    target_gid=target_gid,
                    require_target=require_target,
                )
            )
        return tuple(entries)
    except RuntimeServiceAccessError:
        raise
    except OSError:
        raise RuntimeServiceAccessError("运行时服务命名空间无法安全打开") from None


def _namespace_entry(
    name: str,
    descriptor: int,
    metadata: os.stat_result,
    *,
    root_device: int,
    target_gid: int,
    require_target: bool,
) -> _NamespaceEntry:
    _validate_namespace_metadata(
        metadata,
        root_device=root_device,
        target_gid=target_gid,
        require_target=require_target,
    )
    _require_no_extended_attributes(descriptor)
    return _NamespaceEntry(name=name, descriptor=descriptor, metadata=metadata)


def _publish_namespace_gid(
    entries: tuple[_NamespaceEntry, ...],
    *,
    root: Path,
    target_gid: int,
    namespace_reference: _NamespaceReference | None = None,
) -> None:
    reference = namespace_reference or _namespace_reference
    for entry in entries:
        before = _rebind_namespace_entry(
            entries,
            entry,
            root,
            target_gid=target_gid,
            require_target_gid=False,
            namespace_reference=reference,
        )
        changed = before.st_gid != target_gid
        try:
            if changed:
                os.fchown(entry.descriptor, -1, target_gid)
            os.fsync(entry.descriptor)
        except OSError:
            raise RuntimeServiceAccessError("运行时服务命名空间 GID 发布失败") from None
        after = os.fstat(entry.descriptor)
        if changed:
            _require_namespace_gid_change(before, after)
        else:
            _require_same_namespace_entry(before, after)
        if after.st_gid != target_gid:
            raise RuntimeServiceAccessError("运行时服务命名空间 GID 未达到目标组")
        _validate_namespace_metadata(
            after,
            root_device=entry.metadata.st_dev,
            target_gid=target_gid,
            require_target=False,
        )
        _require_no_extended_attributes(entry.descriptor)
        entry.metadata = after


def _publish_namespace_mode(
    entries: tuple[_NamespaceEntry, ...],
    *,
    root: Path,
    target_gid: int,
    namespace_reference: _NamespaceReference | None = None,
) -> None:
    reference = namespace_reference or _namespace_reference
    for entry in entries:
        before = _rebind_namespace_entry(
            entries,
            entry,
            root,
            target_gid=target_gid,
            require_target_gid=True,
            namespace_reference=reference,
        )
        changed = stat.S_IMODE(before.st_mode) != _NAMESPACE_TARGET_MODE
        try:
            if changed:
                os.fchmod(entry.descriptor, _NAMESPACE_TARGET_MODE)
            os.fsync(entry.descriptor)
        except OSError:
            raise RuntimeServiceAccessError("运行时服务命名空间 mode 发布失败") from None
        after = os.fstat(entry.descriptor)
        if changed:
            _require_namespace_mode_change(before, after)
        else:
            _require_same_namespace_entry(before, after)
        _validate_namespace_metadata(
            after,
            root_device=entry.metadata.st_dev,
            target_gid=target_gid,
            require_target=True,
        )
        _require_no_extended_attributes(entry.descriptor)
        entry.metadata = after


def _verify_namespace_references(
    entries: tuple[_NamespaceEntry, ...],
    root: Path,
    *,
    namespace_reference: _NamespaceReference | None = None,
) -> None:
    reference = namespace_reference or _namespace_reference
    for entry in entries:
        linked = reference(entries, entry, root)
        current = os.fstat(entry.descriptor)
        _require_same_namespace_entry(entry.metadata, current)
        _require_same_namespace_entry(current, linked)


def _rebind_namespace_entry(
    entries: tuple[_NamespaceEntry, ...],
    entry: _NamespaceEntry,
    root: Path,
    *,
    target_gid: int,
    require_target_gid: bool,
    namespace_reference: _NamespaceReference | None = None,
) -> os.stat_result:
    reference = namespace_reference or _namespace_reference
    current = os.fstat(entry.descriptor)
    _require_same_namespace_entry(entry.metadata, current)
    _require_same_namespace_entry(current, reference(entries, entry, root))
    _validate_namespace_metadata(
        current,
        root_device=entries[0].metadata.st_dev,
        target_gid=target_gid,
        require_target=False,
    )
    if require_target_gid and current.st_gid != target_gid:
        raise RuntimeServiceAccessError("运行时服务命名空间 GID 尚未完成发布")
    _require_no_extended_attributes(entry.descriptor)
    return current


def _namespace_reference(
    entries: tuple[_NamespaceEntry, ...],
    entry: _NamespaceEntry,
    root: Path,
) -> os.stat_result:
    return root.lstat() if not entry.name else _stat_at(entries[0].descriptor, entry.name)


def _validate_namespace_metadata(
    metadata: os.stat_result,
    *,
    root_device: int,
    target_gid: int,
    require_target: bool,
) -> None:
    mode = stat.S_IMODE(metadata.st_mode)
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeServiceAccessError("运行时服务命名空间不是目录")
    if metadata.st_dev != root_device or metadata.st_uid != 0:
        raise RuntimeServiceAccessError("运行时服务命名空间所有权不受信任")
    if require_target:
        if metadata.st_gid != target_gid or mode != _NAMESPACE_TARGET_MODE:
            raise RuntimeServiceAccessError("运行时服务命名空间尚未精确发布")
        return
    if metadata.st_gid not in {0, target_gid} or mode not in _NAMESPACE_MIGRATION_MODES:
        raise RuntimeServiceAccessError("运行时服务命名空间迁移状态不受信任")


def _require_no_extended_attributes(descriptor: int) -> None:
    try:
        attributes = os.listxattr(descriptor)
    except (AttributeError, OSError):
        raise RuntimeServiceAccessError("运行时服务命名空间 ACL 无法复验") from None
    if attributes:
        raise RuntimeServiceAccessError("运行时服务命名空间包含 ACL 或扩展属性")


def _open_directory(path: Path | str, *, dir_fd: int | None = None) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NOATIME", 0)
    )
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError:
        raise RuntimeServiceAccessError("运行时服务命名空间目录不可安全打开") from None


def _stat_at(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise RuntimeServiceAccessError("运行时服务命名空间目录项不可安全检查") from None


def _require_same_namespace_entry(
    before: os.stat_result,
    after: os.stat_result,
) -> None:
    if _namespace_identity(before) != _namespace_identity(after):
        raise RuntimeServiceAccessError("运行时服务命名空间身份发生漂移")


def _require_namespace_gid_change(
    before: os.stat_result,
    after: os.stat_result,
) -> None:
    if _namespace_gid_identity(before) != _namespace_gid_identity(after):
        raise RuntimeServiceAccessError("运行时服务命名空间 GID 发布越界")


def _require_namespace_mode_change(
    before: os.stat_result,
    after: os.stat_result,
) -> None:
    if _namespace_mode_identity(before) != _namespace_mode_identity(after):
        raise RuntimeServiceAccessError("运行时服务命名空间 mode 发布越界")


def _namespace_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _namespace_gid_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _namespace_mode_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
