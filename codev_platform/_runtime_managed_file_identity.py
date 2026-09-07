"""受管文件元数据证据与稳定身份校验。"""

from __future__ import annotations

import os
import stat

from codev_platform._runtime_managed_file_contract import (
    ManagedFileError,
    ManagedFileIdentityError,
    ManagedFilePolicy,
)


def require_managed_regular(metadata: object, policy: ManagedFilePolicy) -> None:
    """验证普通叶子的类型、链接、精确权限、属主、属组与大小。"""
    mode = getattr(metadata, "st_mode", -1)
    uid = getattr(metadata, "st_uid", -1)
    gid = getattr(metadata, "st_gid", -1)
    size = getattr(metadata, "st_size", -1)
    links = getattr(metadata, "st_nlink", 0)
    if not stat.S_ISREG(mode) or links != 1:
        raise ManagedFileIdentityError("受管文件必须是单链接普通文件")
    if stat.S_IMODE(mode) != policy.mode:
        raise ManagedFileIdentityError("受管文件权限与策略不一致")
    if policy.require_uid is not None and uid != policy.require_uid:
        raise ManagedFileIdentityError("受管文件属主与策略不一致")
    if policy.require_gid is not None and gid != policy.require_gid:
        raise ManagedFileIdentityError("受管文件属组与策略不一致")
    if type(size) is not int or size < 0 or size > policy.max_bytes:
        raise ManagedFileIdentityError("受管文件大小超出上限")


def file_stability_identity(metadata: object) -> tuple[int, ...]:
    """提取两次 fstat 间必须保持一致的文件身份和安全元数据。"""
    return (
        int(getattr(metadata, "st_dev", -1)),
        int(getattr(metadata, "st_ino", -1)),
        int(getattr(metadata, "st_size", -1)),
        int(getattr(metadata, "st_mode", -1)),
        int(getattr(metadata, "st_uid", -1)),
        int(getattr(metadata, "st_gid", -1)),
        int(getattr(metadata, "st_mtime_ns", -1)),
        int(getattr(metadata, "st_ctime_ns", -1)),
    )


def require_same_current_leaf(
    leaf: str,
    parent_descriptor: int,
    metadata: object,
    policy: ManagedFilePolicy,
) -> None:
    """删除前复核路径仍指向刚刚验证过的同一受管 inode。"""
    linked = os.stat(leaf, dir_fd=parent_descriptor, follow_symlinks=False)
    require_managed_regular(linked, policy)
    if (linked.st_dev, linked.st_ino) != (
        getattr(metadata, "st_dev", -1),
        getattr(metadata, "st_ino", -1),
    ):
        raise ManagedFileError("受管文件精确删除前 inode 已漂移")


__all__ = [
    "file_stability_identity",
    "require_managed_regular",
    "require_same_current_leaf",
]
