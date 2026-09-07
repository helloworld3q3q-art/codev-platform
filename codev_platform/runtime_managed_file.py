"""受管普通文件的兼容 facade；真实 I/O 统一委托给 bound 原语。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from codev_platform._runtime_managed_file_bound import (
    create_managed_bytes_exclusive_at,
    fsync_managed_directory_at,
    open_managed_regular_descriptor_at,
    read_managed_bytes_at,
    read_optional_managed_bytes_at,
    remove_managed_bytes_exact_at,
    verify_managed_regular_descriptor_at,
    write_managed_bytes_atomic_at,
)
from codev_platform._runtime_managed_directory_bound import (
    open_managed_directory_descriptor_at,
    open_optional_managed_directory_descriptor_at,
)
from codev_platform._runtime_managed_file_contract import (
    ManagedFileError,
    ManagedFileEvidence,
    ManagedFileIdentityError,
    ManagedFilePolicy,
    require_managed_policy,
    trusted_uid,
)
from codev_platform._runtime_managed_file_fd import (
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
    read_optional_root_owned_regular_file,
    read_optional_root_owned_regular_file_snapshot,
    remove_root_owned_regular_file,
    write_root_owned_regular_file_atomic,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBinding,
    RuntimeRootBindingError,
    validate_runtime_relative_path,
    validate_runtime_root_path,
)


@contextmanager
def _bind_path_root(root: Path, *, owner_uid: int) -> Iterator[BoundRuntimeRoot]:
    try:
        binding = RuntimeRootBinding(Path(root), owner_uid)
        with binding.bind() as bound_root:
            yield bound_root
    except RuntimeRootBindingError as error:
        raise ManagedFileError("受管根目录无法安全绑定") from error
    except (TypeError, ValueError) as error:
        raise ManagedFileError("受管根目录无效") from error


def _validate_path_before_binding(path: Path, root: Path) -> None:
    try:
        validate_runtime_relative_path(path, root=Path(root))
    except RuntimeRootBindingError as error:
        raise ManagedFileError(str(error)) from error
    except (TypeError, ValueError) as error:
        raise ManagedFileError("受管文件路径无效") from error


def _validate_root_before_binding(root: Path) -> None:
    try:
        validate_runtime_root_path(Path(root))
    except RuntimeRootBindingError as error:
        raise ManagedFileError(f"受管目录路径无效：{error}") from error
    except (TypeError, ValueError) as error:
        raise ManagedFileError("受管目录路径无效") from error


def read_managed_bytes(path: Path, *, root: Path, policy: ManagedFilePolicy) -> bytes:
    """兼容 Path 调用；一次绑定后委托相同的 bound 读取实现。"""
    require_managed_policy(policy)
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=trusted_uid(policy)) as bound_root:
        return read_managed_bytes_at(path, root=bound_root, policy=policy)


def read_optional_managed_bytes(
    path: Path,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> bytes | None:
    """兼容 Path 调用的安全可选读取。"""
    require_managed_policy(policy)
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=trusted_uid(policy)) as bound_root:
        return read_optional_managed_bytes_at(path, root=bound_root, policy=policy)


def write_managed_bytes_atomic(
    path: Path,
    payload: bytes,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """兼容 Path 调用；发布算法只存在于 bound 机械层。"""
    require_managed_policy(policy)
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=trusted_uid(policy)) as bound_root:
        return write_managed_bytes_atomic_at(
            path,
            payload,
            root=bound_root,
            policy=policy,
        )


def create_managed_bytes_exclusive(
    path: Path,
    payload: bytes,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> ManagedFileEvidence:
    """兼容 Path 调用；独占发布算法只存在于 bound 机械层。"""
    require_managed_policy(policy)
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=trusted_uid(policy)) as bound_root:
        return create_managed_bytes_exclusive_at(
            path,
            payload,
            root=bound_root,
            policy=policy,
        )


def remove_managed_bytes_exact(
    path: Path,
    expected: bytes,
    *,
    root: Path,
    policy: ManagedFilePolicy,
) -> bool:
    """兼容 Path 调用的逐字节匹配删除。"""
    require_managed_policy(policy)
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=trusted_uid(policy)) as bound_root:
        return remove_managed_bytes_exact_at(
            path,
            expected,
            root=bound_root,
            policy=policy,
        )


def fsync_managed_directory(path: Path) -> None:
    """兼容 Path 调用；仅同步一次绑定得到的根 descriptor。"""
    _validate_root_before_binding(path)
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise ManagedFileError("受管文件 descriptor-safe 操作只支持 POSIX")
    with _bind_path_root(path, owner_uid=int(getter())) as bound_root:
        fsync_managed_directory_at(bound_root)


def open_managed_regular_descriptor(
    path: Path,
    *,
    root: Path,
    mode: int,
    read_only: bool,
    append: bool = False,
    read_write: bool = False,
    allowed_existing_modes: frozenset[int] | None = None,
) -> tuple[int, bool]:
    """兼容 Path 调用的锁文件 descriptor 打开。"""
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise ManagedFileError("受管文件 descriptor-safe 操作只支持 POSIX")
    _validate_path_before_binding(path, root)
    with _bind_path_root(root, owner_uid=int(getter())) as bound_root:
        return open_managed_regular_descriptor_at(
            path,
            root=bound_root,
            mode=mode,
            read_only=read_only,
            append=append,
            read_write=read_write,
            allowed_existing_modes=allowed_existing_modes,
        )


__all__ = [
    "BoundRuntimeRoot",
    "ManagedFileError",
    "ManagedFileEvidence",
    "ManagedFileIdentityError",
    "ManagedFilePolicy",
    "RootOwnedRegularFileSnapshot",
    "TrustedManagedPathError",
    "_close_quietly",
    "_fchmod",
    "_fchown",
    "_fstat",
    "_fsync",
    "_open_optional_regular_file",
    "_open_temporary_file",
    "_open_trusted_parent",
    "_read_bounded",
    "_read_file_flags",
    "_require_write_arguments",
    "_root_owned_regular_file_metadata",
    "_supports_dir_fd",
    "_temporary_file_flags",
    "_unlink_at",
    "_unlink_temporary_quietly",
    "_write_all",
    "create_managed_bytes_exclusive",
    "create_managed_bytes_exclusive_at",
    "fsync_managed_directory",
    "fsync_managed_directory_at",
    "open_managed_regular_descriptor",
    "open_managed_regular_descriptor_at",
    "open_managed_directory_descriptor_at",
    "open_optional_managed_directory_descriptor_at",
    "read_managed_bytes",
    "read_managed_bytes_at",
    "read_optional_managed_bytes",
    "read_optional_managed_bytes_at",
    "read_optional_root_owned_regular_file",
    "read_optional_root_owned_regular_file_snapshot",
    "remove_managed_bytes_exact",
    "remove_managed_bytes_exact_at",
    "remove_root_owned_regular_file",
    "verify_managed_regular_descriptor_at",
    "write_managed_bytes_atomic",
    "write_managed_bytes_atomic_at",
    "write_root_owned_regular_file_atomic",
]
