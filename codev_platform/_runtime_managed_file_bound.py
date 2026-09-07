"""以 ``BoundRuntimeRoot`` 为能力的受管普通文件机械实现。"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from codev_platform._runtime_managed_file_contract import (
    ManagedFileError,
    ManagedFileEvidence,
    ManagedFileIdentityError,
    ManagedFilePolicy,
    require_managed_payload,
    require_managed_policy,
    trusted_uid,
)
from codev_platform._runtime_managed_directory_bound import (
    ManagedDirectoryMissingError,
    open_managed_parent_at as _open_managed_parent_at,
)
from codev_platform._runtime_managed_file_fd import (
    _close_on_exec_flag,
    _close_quietly,
    _fchmod,
    _fchown,
    _fstat,
    _fsync,
    _open_at,
    _open_temporary_file,
    _read_bounded,
    _read_file_flags,
    _replace_at,
    _required_flag,
    _unlink_at,
    _unlink_temporary_quietly,
    _write_all,
)
from codev_platform._runtime_managed_file_identity import (
    file_stability_identity as _file_stability_identity,
    require_managed_regular as _require_managed_regular,
    require_same_current_leaf as _require_same_current_leaf,
)
from codev_platform._runtime_managed_file_lock import lock_managed_parent as _lock_managed_parent
from codev_platform._runtime_renameat2 import rename_noreplace_at as _rename_noreplace_at
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError


def read_managed_bytes_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> bytes:
    """从持有的根 descriptor 相对读取受管普通文件。"""
    relative = _prepare_operation(path, root, policy)
    parent_descriptor: int | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=False,
            trusted_owner=trusted_uid(policy),
        )
        content, _metadata = _read_managed_from_parent(leaf, parent_descriptor, policy)
        return content
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全读取") from error
    finally:
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def read_optional_managed_bytes_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
    allow_missing_parent: bool = False,
) -> bytes | None:
    """可选读取；仅显式授权时才把安全缺失父目录视为不存在。"""
    if type(allow_missing_parent) is not bool:
        raise ManagedFileError("allow_missing_parent 必须是 bool")
    relative = _prepare_operation(path, root, policy)
    parent_descriptor: int | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=False,
            trusted_owner=trusted_uid(policy),
        )
        return _read_optional_managed_from_parent(leaf, parent_descriptor, policy)
    except ManagedDirectoryMissingError:
        if allow_missing_parent:
            return None
        raise ManagedFileError("受管文件父目录不存在") from None
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全读取") from error
    finally:
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def write_managed_bytes_atomic_at(
    path: Path,
    payload: bytes,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """在同一受信父目录锁内完整写入后原子替换叶子。"""
    relative = _prepare_operation(path, root, policy)
    content = require_managed_payload(payload, policy)
    parent_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=True,
            trusted_owner=trusted_uid(policy),
        )
        _lock_managed_parent(parent_descriptor)
        _require_replaceable_leaf(leaf, parent_descriptor, policy)
        temporary_name, temporary_descriptor = _open_temporary_file(
            parent_descriptor,
            policy.mode,
        )
        _write_managed_descriptor(temporary_descriptor, content, policy)
        root.verify_visible()
        _replace_at(temporary_name, leaf, parent_descriptor, parent_descriptor)
        temporary_name = None
        _fsync(parent_descriptor)
        return _verify_published_file(leaf, parent_descriptor, relative, content, policy)
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全原子写入") from error
    finally:
        if temporary_name is not None and parent_descriptor is not None:
            _unlink_temporary_quietly(temporary_name, parent_descriptor)
        if temporary_descriptor is not None:
            _close_quietly(temporary_descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def create_managed_bytes_exclusive_at(
    path: Path,
    payload: bytes,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """以 ``RENAME_NOREPLACE`` 发布已完整同步的不可变叶子。"""
    relative = _prepare_operation(path, root, policy)
    content = require_managed_payload(payload, policy)
    parent_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=True,
            trusted_owner=trusted_uid(policy),
        )
        _lock_managed_parent(parent_descriptor)
        temporary_name, temporary_descriptor = _open_temporary_file(
            parent_descriptor,
            policy.mode,
        )
        _write_managed_descriptor(temporary_descriptor, content, policy)
        root.verify_visible()
        _rename_noreplace_at(temporary_name, leaf, parent_descriptor, parent_descriptor)
        temporary_name = None
        _fsync(parent_descriptor)
        return _verify_published_file(leaf, parent_descriptor, relative, content, policy)
    except FileExistsError:
        raise
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全独占创建") from error
    finally:
        if temporary_name is not None and parent_descriptor is not None:
            _unlink_temporary_quietly(temporary_name, parent_descriptor)
        if temporary_descriptor is not None:
            _close_quietly(temporary_descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def remove_managed_bytes_exact_at(
    path: Path,
    expected: bytes,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> bool:
    """仅在受信父目录中逐字节匹配后删除叶子并同步目录。"""
    relative = _prepare_operation(path, root, policy)
    content = require_managed_payload(expected, policy)
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=False,
            trusted_owner=trusted_uid(policy),
        )
        _lock_managed_parent(parent_descriptor)
        try:
            descriptor = _open_at(leaf, _read_file_flags(), dir_fd=parent_descriptor)
        except FileNotFoundError:
            return False
        before = _fstat(descriptor)
        _require_managed_regular(before, policy)
        actual = _read_bounded(descriptor, policy.max_bytes)
        after = _fstat(descriptor)
        _require_managed_regular(after, policy)
        if _file_stability_identity(before) != _file_stability_identity(after):
            raise ManagedFileError("受管文件读取期间发生摘要漂移")
        if actual != content:
            raise ManagedFileError("受管文件内容与精确删除期望不一致")
        _require_same_current_leaf(leaf, parent_descriptor, after, policy)
        root.verify_visible()
        _unlink_at(leaf, parent_descriptor)
        _fsync(parent_descriptor)
        return True
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全精确删除") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def open_managed_regular_descriptor_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    mode: int,
    read_only: bool,
    append: bool = False,
    create_missing: bool = False,
    read_write: bool = False,
    allowed_existing_modes: frozenset[int] | None = None,
) -> tuple[int, bool]:
    """在同一根租约内安全创建或重开锁等持续持有的普通文件。"""
    if type(read_only) is not bool or type(append) is not bool or type(read_write) is not bool:
        raise ManagedFileError("受管文件打开模式无效")
    if read_only and read_write:
        raise ManagedFileError("受管文件不能同时请求只读和读写模式")
    if type(create_missing) is not bool:
        raise ManagedFileError("受管文件父目录创建策略无效")
    _require_allowed_existing_modes(allowed_existing_modes)
    policy = ManagedFilePolicy(
        mode=mode,
        require_uid=root.owner_uid,
        max_bytes=(1 << 63) - 1,
    )
    relative = _prepare_operation(path, root, policy)
    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=create_missing,
            trusted_owner=root.owner_uid,
        )
        access = os.O_RDONLY if read_only else os.O_RDWR if read_write else os.O_WRONLY
        flags = access | _required_flag("O_NOFOLLOW") | _close_on_exec_flag()
        flags |= getattr(os, "O_NONBLOCK", 0)
        if append:
            flags |= os.O_APPEND
        try:
            descriptor = _open_at(
                leaf,
                flags | os.O_CREAT | os.O_EXCL,
                dir_fd=parent_descriptor,
                mode=mode,
            )
            created = True
            _fchown(descriptor, root.owner_uid, -1)
            _fchmod(descriptor, mode)
            _fsync(descriptor)
            _fsync(parent_descriptor)
        except FileExistsError:
            descriptor = _open_at(leaf, flags, dir_fd=parent_descriptor)
            created = False
        _require_opened_regular(
            _fstat(descriptor),
            policy,
            created=created,
            allowed_existing_modes=allowed_existing_modes,
        )
        root.verify_visible()
        opened = descriptor
        descriptor = None
        return opened, created
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全打开") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)


def verify_managed_regular_descriptor_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    descriptor: int,
    mode: int,
) -> None:
    """证明锁 descriptor 仍对应同一受信父目录中的规范叶子。"""
    if type(descriptor) is not int or descriptor < 0:
        raise ManagedFileIdentityError("受管文件 descriptor 无效")
    policy = ManagedFilePolicy(
        mode=mode,
        require_uid=root.owner_uid,
        max_bytes=(1 << 63) - 1,
    )
    relative = _prepare_operation(path, root, policy)
    parent_descriptor: int | None = None
    try:
        parent_descriptor, leaf = _open_managed_parent_at(
            root,
            relative,
            create_missing=False,
            trusted_owner=root.owner_uid,
        )
        linked = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
        opened = _fstat(descriptor)
        _require_managed_regular(linked, policy)
        _require_managed_regular(opened, policy)
        if (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino):
            raise ManagedFileIdentityError("受管文件所有权、权限或 inode 不安全")
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileIdentityError("受管文件所有权、权限或 inode 不安全") from error
    finally:
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)
        root.verify_visible()


def fsync_managed_directory_at(root: BoundRuntimeRoot) -> None:
    """同步已绑定根目录本身，不按 pathname 重新打开真实 I/O 根。"""
    root.verify_visible()
    descriptor: int | None = None
    try:
        descriptor = root._duplicate_root_fd()
        _fsync(descriptor)
    except RuntimeRootBindingError:
        raise
    except OSError as error:
        raise ManagedFileError("受管目录无法安全同步") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        root.verify_visible()


def _prepare_operation(
    path: Path,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> Path:
    require_managed_policy(policy)
    root.verify_visible()
    return Path(*root.relative_path(path))


def _require_allowed_existing_modes(allowed_existing_modes: frozenset[int] | None) -> None:
    if allowed_existing_modes is None:
        return
    if type(allowed_existing_modes) is not frozenset or not allowed_existing_modes:
        raise ManagedFileError("受管文件兼容权限策略无效")
    for mode in allowed_existing_modes:
        if type(mode) is not int or mode < 0 or mode > 0o777:
            raise ManagedFileError("受管文件兼容权限策略无效")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ManagedFileError("受管文件兼容权限不能允许非所有者写入")


def _require_opened_regular(
    metadata: object,
    policy: ManagedFilePolicy,
    *,
    created: bool,
    allowed_existing_modes: frozenset[int] | None,
) -> None:
    if created or allowed_existing_modes is None:
        _require_managed_regular(metadata, policy)
        return
    mode = stat.S_IMODE(getattr(metadata, "st_mode", -1))
    if mode not in allowed_existing_modes:
        raise ManagedFileIdentityError("受管文件权限与兼容策略不一致")
    _require_managed_regular(
        metadata,
        ManagedFilePolicy(
            mode=mode,
            require_uid=policy.require_uid,
            require_gid=policy.require_gid,
            max_bytes=policy.max_bytes,
        ),
    )


def _require_replaceable_leaf(
    leaf: str,
    parent_descriptor: int,
    policy: ManagedFilePolicy,
) -> None:
    descriptor: int | None = None
    try:
        descriptor = _open_at(leaf, _read_file_flags(), dir_fd=parent_descriptor)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ManagedFileError("受管文件目标含符号链接或不是安全普通文件") from error
    try:
        _require_managed_regular(_fstat(descriptor), policy)
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _read_optional_managed_from_parent(
    leaf: str,
    parent_descriptor: int,
    policy: ManagedFilePolicy,
) -> bytes | None:
    try:
        return _read_managed_from_parent(leaf, parent_descriptor, policy)[0]
    except FileNotFoundError:
        return None


def _read_managed_from_parent(
    leaf: str,
    parent_descriptor: int,
    policy: ManagedFilePolicy,
) -> tuple[bytes, object]:
    descriptor: int | None = None
    try:
        descriptor = _open_at(leaf, _read_file_flags(), dir_fd=parent_descriptor)
        before = _fstat(descriptor)
        _require_managed_regular(before, policy)
        content = _read_bounded(descriptor, policy.max_bytes)
        after = _fstat(descriptor)
        _require_managed_regular(after, policy)
        if _file_stability_identity(before) != _file_stability_identity(after):
            raise ManagedFileError("受管文件读取期间发生摘要漂移")
        return content, after
    except FileNotFoundError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管文件无法安全读取") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _write_managed_descriptor(
    descriptor: int,
    payload: bytes,
    policy: ManagedFilePolicy,
) -> None:
    _write_all(descriptor, payload)
    if policy.require_uid is not None or policy.require_gid is not None:
        _fchown(
            descriptor,
            policy.require_uid if policy.require_uid is not None else -1,
            policy.require_gid if policy.require_gid is not None else -1,
        )
    _fchmod(descriptor, policy.mode)
    _fsync(descriptor)


def _verify_published_file(
    leaf: str,
    parent_descriptor: int,
    relative: Path,
    expected: bytes,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    actual, metadata = _read_managed_from_parent(leaf, parent_descriptor, policy)
    if actual != expected:
        raise ManagedFileError("受管文件发布后摘要漂移")
    return ManagedFileEvidence(
        path=relative.as_posix(),
        sha256=hashlib.sha256(actual).hexdigest(),
        size=len(actual),
        mode=stat.S_IMODE(getattr(metadata, "st_mode", -1)),
        uid=int(getattr(metadata, "st_uid", -1)),
        gid=int(getattr(metadata, "st_gid", -1)),
    )


__all__ = [
    "create_managed_bytes_exclusive_at",
    "fsync_managed_directory_at",
    "open_managed_regular_descriptor_at",
    "read_managed_bytes_at",
    "read_optional_managed_bytes_at",
    "remove_managed_bytes_exact_at",
    "verify_managed_regular_descriptor_at",
    "write_managed_bytes_atomic_at",
]
