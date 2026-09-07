"""受绑定运行时根下的受管目录 descriptor 机械层。"""

from __future__ import annotations

import stat
from pathlib import Path

from codev_platform._runtime_managed_file_contract import ManagedFileError
from codev_platform._runtime_managed_file_fd import (
    _close_quietly,
    _directory_flags,
    _fstat,
    _fsync,
    _mkdir_at,
    _open_at,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError


_CREATED_DIRECTORY_MODE = 0o755
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class ManagedDirectoryMissingError(ManagedFileError):
    """受管目录链中显式允许缺失的组件不存在。"""


def open_managed_parent_at(
    root: BoundRuntimeRoot,
    relative: Path,
    *,
    create_missing: bool,
    trusted_owner: int,
) -> tuple[int, str]:
    """打开受管叶子所在父目录，叶子名称不参与目录遍历。"""
    if not relative.parts:
        raise ManagedFileError("受管文件路径必须包含叶子名称")
    descriptor = _open_managed_directory_components(
        root,
        relative.parts[:-1],
        create_missing=create_missing,
        trusted_owner=trusted_owner,
    )
    return descriptor, relative.parts[-1]


def open_managed_directory_descriptor_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    create_missing: bool,
) -> int:
    """以同一根租约安全打开或创建一个受管目录并交还 descriptor。"""
    if type(create_missing) is not bool:
        raise ManagedFileError("受管目录创建策略无效")
    descriptor: int | None = None
    try:
        root.verify_visible()
        components = root.relative_path(path)
        descriptor = _open_managed_directory_components(
            root,
            components,
            create_missing=create_missing,
            trusted_owner=root.owner_uid,
        )
        root.verify_visible()
        opened = descriptor
        descriptor = None
        return opened
    except RuntimeRootBindingError:
        raise
    except ManagedFileError:
        raise
    except OSError as error:
        raise ManagedFileError("受管目录无法安全打开") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def open_optional_managed_directory_descriptor_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
) -> int | None:
    """安全打开可选目录；只将缺失目录映射为空。"""
    try:
        return open_managed_directory_descriptor_at(
            path,
            root=root,
            create_missing=False,
        )
    except ManagedDirectoryMissingError:
        return None


def _open_managed_directory_components(
    root: BoundRuntimeRoot,
    components: tuple[str, ...],
    *,
    create_missing: bool,
    trusted_owner: int,
) -> int:
    descriptor = root._duplicate_root_fd()
    try:
        _require_managed_directory(_fstat(descriptor), trusted_owner=trusted_owner)
        for component in components:
            child = _open_managed_directory_component(
                component,
                descriptor,
                create_missing=create_missing,
                trusted_owner=trusted_owner,
            )
            _close_quietly(descriptor)
            descriptor = child
        opened = descriptor
        descriptor = None
        return opened
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _open_managed_directory_component(
    name: str,
    parent_descriptor: int,
    *,
    create_missing: bool,
    trusted_owner: int,
) -> int:
    try:
        descriptor = _open_at(name, _directory_flags(), dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not create_missing:
            raise ManagedDirectoryMissingError("受管文件父目录不存在") from None
        try:
            _mkdir_at(name, _CREATED_DIRECTORY_MODE, parent_descriptor)
        except FileExistsError:
            pass
        except OSError as error:
            raise ManagedFileError("受管文件父目录无法安全创建") from error
        try:
            descriptor = _open_at(name, _directory_flags(), dir_fd=parent_descriptor)
        except OSError as error:
            raise ManagedFileError("受管文件父目录无法安全打开") from error
    except OSError as error:
        raise ManagedFileError("受管文件父目录含符号链接或无法安全打开") from error
    try:
        _require_managed_directory(_fstat(descriptor), trusted_owner=trusted_owner)
        if create_missing:
            _fsync(parent_descriptor)
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _require_managed_directory(metadata: object, *, trusted_owner: int) -> None:
    mode = getattr(metadata, "st_mode", -1)
    uid = getattr(metadata, "st_uid", -1)
    if not stat.S_ISDIR(mode) or uid != trusted_owner or stat.S_IMODE(mode) & _UNSAFE_WRITE_BITS:
        raise ManagedFileError("受管文件父目录属主或权限不安全")


__all__ = [
    "ManagedDirectoryMissingError",
    "open_managed_directory_descriptor_at",
    "open_managed_parent_at",
    "open_optional_managed_directory_descriptor_at",
]
