"""运行时 fd tree 的对象元数据与身份稳定性验证。"""

from __future__ import annotations

import os
import stat
import sys

from codev_platform._runtime_fd_tree_contracts import (
    ConvergeCompletedAccessRepair,
    PreflightCompletedAccessRepair,
    PreflightGroup,
    PublishGroup,
    RuntimeFdGroupOperation,
    RuntimeFdModePolicy,
    RuntimeFdTreeError,
    RuntimeFdTreeOperation,
    VerifyGroup,
)


_SPECIAL_PERMISSION_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX


def _validate_group_metadata(
    metadata: os.stat_result,
    operation: RuntimeFdGroupOperation,
    *,
    require_target: bool,
) -> None:
    if type(operation) not in {PreflightGroup, PublishGroup, VerifyGroup}:
        raise RuntimeFdTreeError("运行时对象树 GID 操作无效")
    target_gid = operation.group_policy.target_gid
    if stat.S_ISLNK(metadata.st_mode):
        if metadata.st_gid != 0:
            raise RuntimeFdTreeError("运行时符号链接必须保持 root 组")
        return
    if require_target:
        if metadata.st_gid != target_gid:
            raise RuntimeFdTreeError("运行时对象尚未发布到目标服务组")
        return
    if metadata.st_gid not in {0, target_gid}:
        raise RuntimeFdTreeError("运行时对象服务组不受信任")


def _group_requires_target(
    operation: RuntimeFdTreeOperation,
    publish_incomplete: bool | None,
) -> bool:
    return type(operation) is VerifyGroup or (
        type(operation) is PublishGroup and publish_incomplete is False
    )


def _validate_entry_metadata(
    metadata: os.stat_result,
    *,
    root_device: int,
    require_canonical: bool,
    mode_policy: RuntimeFdModePolicy,
) -> None:
    mode = metadata.st_mode
    is_symlink = stat.S_ISLNK(mode)
    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode) or is_symlink):
        raise RuntimeFdTreeError("运行时对象树包含特殊文件")
    if metadata.st_dev != root_device:
        raise RuntimeFdTreeError("运行时对象树跨越设备边界")
    if metadata.st_uid != 0:
        raise RuntimeFdTreeError("运行时对象必须由 root 所有")
    if is_symlink:
        return
    permissions = stat.S_IMODE(mode)
    if permissions & _SPECIAL_PERMISSION_BITS:
        raise RuntimeFdTreeError("运行时对象包含特殊权限位")
    if permissions & 0o022:
        raise RuntimeFdTreeError("运行时对象允许非 root 写入")
    if stat.S_ISDIR(mode) and permissions & 0o500 != 0o500:
        raise RuntimeFdTreeError("运行时目录缺少既有 root 访问权限")
    if stat.S_ISREG(mode) and permissions & 0o400 != 0o400:
        raise RuntimeFdTreeError("运行时普通文件缺少既有 root 访问权限")
    if stat.S_ISREG(mode) and metadata.st_nlink != 1:
        raise RuntimeFdTreeError("运行时普通文件不能是硬链接")
    if require_canonical and permissions != mode_policy.canonical_mode(mode):
        raise RuntimeFdTreeError("运行时对象访问模式漂移")


def _validate_completed_access_repair_metadata(
    metadata: os.stat_result,
    *,
    root_device: int,
    operation: PreflightCompletedAccessRepair | ConvergeCompletedAccessRepair,
) -> None:
    """仅接受可安全收敛的已完成对象访问投影。"""
    if type(operation) not in {
        PreflightCompletedAccessRepair,
        ConvergeCompletedAccessRepair,
    }:
        raise RuntimeFdTreeError("已完成对象访问修复操作无效")
    _validate_entry_metadata(
        metadata,
        root_device=root_device,
        require_canonical=False,
        mode_policy=operation.mode_policy,
    )
    if stat.S_ISLNK(metadata.st_mode):
        return
    permissions = stat.S_IMODE(metadata.st_mode)
    canonical = operation.mode_policy.canonical_mode(metadata.st_mode)
    if stat.S_ISDIR(metadata.st_mode) and permissions in {canonical, 0o755}:
        return
    if stat.S_ISREG(metadata.st_mode) and permissions == canonical:
        return
    raise RuntimeFdTreeError("已完成对象访问模式不受支持")


def _require_no_extended_attributes(descriptor: int) -> None:
    try:
        attributes = os.listxattr(descriptor)
    except (AttributeError, OSError):
        raise RuntimeFdTreeError("运行时对象扩展属性或 ACL 无法复验") from None
    if attributes:
        raise RuntimeFdTreeError("运行时对象包含扩展属性或 ACL")


def _require_linux_root() -> None:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise RuntimeFdTreeError("运行时对象访问策略仅支持 Linux")
    if os.geteuid() != 0:
        raise RuntimeFdTreeError("运行时对象只能由 Linux root 定型")


def _require_same_entry(
    before: os.stat_result,
    after: os.stat_result | None,
    *,
    label: str,
) -> None:
    if after is None or _entry_identity(before) != _entry_identity(after):
        raise RuntimeFdTreeError(f"{label}身份发生漂移")


def _require_stable_content(before: os.stat_result, after: os.stat_result) -> None:
    if _content_identity(before) != _content_identity(after):
        raise RuntimeFdTreeError("运行时对象定型期间内容身份发生漂移")


def _require_stable_group_content(
    before: os.stat_result,
    after: os.stat_result,
) -> None:
    if _group_content_identity(before) != _group_content_identity(after):
        raise RuntimeFdTreeError("运行时对象发布期间内容身份发生漂移")


def _entry_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _content_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _group_content_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


__all__ = [
    "_content_identity",
    "_entry_identity",
    "_group_content_identity",
    "_group_requires_target",
    "_require_linux_root",
    "_require_no_extended_attributes",
    "_require_same_entry",
    "_require_stable_content",
    "_require_stable_group_content",
    "_validate_entry_metadata",
    "_validate_completed_access_repair_metadata",
    "_validate_group_metadata",
]
