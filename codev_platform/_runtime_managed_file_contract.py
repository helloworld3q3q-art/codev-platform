"""受管普通文件的值对象、错误与无状态参数校验。"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from codev_platform._runtime_managed_file_fd import ManagedFileError


_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


@dataclass(frozen=True, slots=True)
class ManagedFilePolicy:
    """受管普通文件的精确权限、属主、属组和大小边界。"""

    mode: int
    require_uid: int | None
    max_bytes: int
    require_gid: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.mode) is not int
            or self.mode < 0
            or self.mode > 0o777
            or not self.mode & stat.S_IRUSR
            or self.mode & _UNSAFE_WRITE_BITS
        ):
            raise ManagedFileError("受管文件权限策略无效或允许非属主写入")
        if self.require_uid is not None and (
            type(self.require_uid) is not int or self.require_uid < 0
        ):
            raise ManagedFileError("受管文件属主策略无效")
        if self.require_gid is not None and (
            type(self.require_gid) is not int or self.require_gid < 0
        ):
            raise ManagedFileError("受管文件属组策略无效")
        if type(self.max_bytes) is not int or self.max_bytes <= 0:
            raise ManagedFileError("受管文件读取上限无效")


@dataclass(frozen=True, slots=True)
class ManagedFileEvidence:
    """由最终受信描述符生成的非秘密文件证据。"""

    path: str
    sha256: str
    size: int
    mode: int
    uid: int
    gid: int


class ManagedFileIdentityError(ManagedFileError):
    """受管叶子文件的类型、链接数、权限或属主不满足策略。"""


def require_managed_policy(policy: ManagedFilePolicy) -> None:
    """拒绝伪造的策略对象，避免放宽文件安全边界。"""
    if type(policy) is not ManagedFilePolicy:
        raise ManagedFileError("受管文件策略类型无效")


def require_managed_payload(payload: bytes, policy: ManagedFilePolicy) -> bytes:
    """验证待发布载荷的类型和上限。"""
    require_managed_policy(policy)
    if type(payload) is not bytes:
        raise ManagedFileError("受管文件内容必须是 bytes")
    if len(payload) > policy.max_bytes:
        raise ManagedFileError("受管文件内容超出上限")
    return payload


def trusted_uid(policy: ManagedFilePolicy) -> int:
    """返回目录和叶子都必须使用的受信属主。"""
    require_managed_policy(policy)
    if policy.require_uid is not None:
        return policy.require_uid
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise ManagedFileError("受管文件 descriptor-safe 操作只支持 POSIX")
    return int(getter())


__all__ = [
    "ManagedFileError",
    "ManagedFileEvidence",
    "ManagedFileIdentityError",
    "ManagedFilePolicy",
    "require_managed_payload",
    "require_managed_policy",
    "trusted_uid",
]
