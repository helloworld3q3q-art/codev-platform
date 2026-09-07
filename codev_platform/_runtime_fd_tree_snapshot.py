"""运行时 fd tree 的不透明结构快照与稳定摘要编码。"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass, field
from typing import Any


_SNAPSHOT_SEAL = object()
_ACCESS_REPAIR_SNAPSHOT_SEAL = object()


class RuntimeFdTreeSnapshotError(RuntimeError):
    """结构快照无法安全构造。"""


@dataclass(frozen=True, slots=True, init=False)
class RuntimeFdTreeAccessRepairSnapshot:
    """仅供已完成对象 mode 收敛使用的不透明前置快照。"""

    _identity_digest: bytes = field(repr=False)
    entries: int
    total_bytes: int
    root_device: int
    root_inode: int
    _seal: object = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("已完成对象访问修复快照只能由遍历器生成")

    @property
    def identity_sha256(self) -> str:
        """返回排除允许收敛 mode 与 ctime 的结构身份摘要。"""
        return self._identity_digest.hex()


@dataclass(frozen=True, slots=True, init=False)
class RuntimeFdTreeSnapshot:
    """仅能由快照模块生成的不透明结构身份快照。"""

    _identity_digest: bytes = field(repr=False)
    entries: int
    total_bytes: int
    root_device: int
    root_inode: int
    _seal: object = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("运行时对象身份快照只能由遍历器生成")

    @property
    def identity_sha256(self) -> str:
        """返回不含路径、GID 或 ctime 的结构身份摘要。"""
        return self._identity_digest.hex()


def new_identity_snapshot(
    *,
    identity_digest: bytes,
    entries: int,
    total_bytes: int,
    root_device: int,
    root_inode: int,
) -> RuntimeFdTreeSnapshot:
    """由已验证的 walker 结果构造封闭快照。"""
    if (
        type(identity_digest) is not bytes
        or len(identity_digest) != hashlib.sha256().digest_size
        or type(entries) is not int
        or entries <= 0
        or type(total_bytes) is not int
        or total_bytes < 0
        or type(root_device) is not int
        or root_device < 0
        or type(root_inode) is not int
        or root_inode <= 0
    ):
        raise RuntimeFdTreeSnapshotError("运行时对象身份快照无效")
    snapshot = object.__new__(RuntimeFdTreeSnapshot)
    object.__setattr__(snapshot, "_identity_digest", identity_digest)
    object.__setattr__(snapshot, "entries", entries)
    object.__setattr__(snapshot, "total_bytes", total_bytes)
    object.__setattr__(snapshot, "root_device", root_device)
    object.__setattr__(snapshot, "root_inode", root_inode)
    object.__setattr__(snapshot, "_seal", _SNAPSHOT_SEAL)
    return snapshot


def new_access_repair_snapshot(
    *,
    identity_digest: bytes,
    entries: int,
    total_bytes: int,
    root_device: int,
    root_inode: int,
) -> RuntimeFdTreeAccessRepairSnapshot:
    """由已完成对象修复预检的 walker 结果构造封闭快照。"""
    _require_snapshot_fields(
        identity_digest=identity_digest,
        entries=entries,
        total_bytes=total_bytes,
        root_device=root_device,
        root_inode=root_inode,
    )
    snapshot = object.__new__(RuntimeFdTreeAccessRepairSnapshot)
    object.__setattr__(snapshot, "_identity_digest", identity_digest)
    object.__setattr__(snapshot, "entries", entries)
    object.__setattr__(snapshot, "total_bytes", total_bytes)
    object.__setattr__(snapshot, "root_device", root_device)
    object.__setattr__(snapshot, "root_inode", root_inode)
    object.__setattr__(snapshot, "_seal", _ACCESS_REPAIR_SNAPSHOT_SEAL)
    return snapshot


def require_identity_snapshot(snapshot: RuntimeFdTreeSnapshot) -> None:
    """拒绝调用方自行构造或篡改的快照实例。"""
    if (
        type(snapshot) is not RuntimeFdTreeSnapshot
        or getattr(snapshot, "_seal", None) is not _SNAPSHOT_SEAL
    ):
        raise TypeError("运行时对象身份快照不受信任")
    try:
        trusted = new_identity_snapshot(
            identity_digest=snapshot._identity_digest,
            entries=snapshot.entries,
            total_bytes=snapshot.total_bytes,
            root_device=snapshot.root_device,
            root_inode=snapshot.root_inode,
        )
    except (AttributeError, RuntimeFdTreeSnapshotError):
        raise TypeError("运行时对象身份快照不受信任") from None
    if trusted != snapshot:
        raise TypeError("运行时对象身份快照不受信任")


def require_access_repair_snapshot(snapshot: RuntimeFdTreeAccessRepairSnapshot) -> None:
    """拒绝调用方伪造已完成对象访问修复快照。"""
    if (
        type(snapshot) is not RuntimeFdTreeAccessRepairSnapshot
        or getattr(snapshot, "_seal", None) is not _ACCESS_REPAIR_SNAPSHOT_SEAL
    ):
        raise TypeError("已完成对象访问修复快照不受信任")
    try:
        trusted = new_access_repair_snapshot(
            identity_digest=snapshot._identity_digest,
            entries=snapshot.entries,
            total_bytes=snapshot.total_bytes,
            root_device=snapshot.root_device,
            root_inode=snapshot.root_inode,
        )
    except (AttributeError, RuntimeFdTreeSnapshotError):
        raise TypeError("已完成对象访问修复快照不受信任") from None
    if trusted != snapshot:
        raise TypeError("已完成对象访问修复快照不受信任")


def encode_snapshot_name(name: str) -> bytes:
    """按文件系统编码生成稳定且不泄露到证明对象的名称字节。"""
    if type(name) is not str:
        raise RuntimeFdTreeSnapshotError("运行时对象身份名称无效")
    return os.fsencode(name)


def entry_snapshot_digest(
    name: str,
    metadata: os.stat_result,
    *,
    child_digests: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    """生成排除目录/普通文件 GID 与 ctime 的层级身份摘要。"""
    digest = hashlib.sha256()
    digest.update(b"codev-runtime-fd-tree-v1\0")
    _update_digest(digest, encode_snapshot_name(name))
    if stat.S_ISDIR(metadata.st_mode):
        kind = b"directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = b"regular"
    elif stat.S_ISLNK(metadata.st_mode):
        kind = b"symlink"
    else:
        raise RuntimeFdTreeSnapshotError("运行时对象身份类型无效")
    _update_digest(digest, kind)
    invariant = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    for value in invariant:
        _update_digest(digest, str(value).encode("ascii"))
    if kind == b"symlink":
        _update_digest(digest, str(metadata.st_gid).encode("ascii"))
        _update_digest(digest, str(metadata.st_ctime_ns).encode("ascii"))
    for child_name, child_digest in child_digests:
        if type(child_name) is not bytes or type(child_digest) is not bytes:
            raise RuntimeFdTreeSnapshotError("运行时对象子树身份无效")
        _update_digest(digest, child_name)
        _update_digest(digest, child_digest)
    return digest.digest()


def access_repair_entry_snapshot_digest(
    name: str,
    metadata: os.stat_result,
    *,
    child_digests: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    """生成只允许目录 mode/ctime 收敛的已完成对象身份摘要。"""
    digest = hashlib.sha256()
    digest.update(b"codev-runtime-fd-tree-access-repair-v1\0")
    _update_digest(digest, encode_snapshot_name(name))
    if stat.S_ISDIR(metadata.st_mode):
        kind = b"directory"
    elif stat.S_ISREG(metadata.st_mode):
        kind = b"regular"
    elif stat.S_ISLNK(metadata.st_mode):
        kind = b"symlink"
    else:
        raise RuntimeFdTreeSnapshotError("已完成对象访问修复身份类型无效")
    _update_digest(digest, kind)
    invariant = (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    for value in invariant:
        _update_digest(digest, str(value).encode("ascii"))
    if kind == b"symlink":
        _update_digest(digest, str(metadata.st_mode).encode("ascii"))
        _update_digest(digest, str(metadata.st_ctime_ns).encode("ascii"))
    for child_name, child_digest in child_digests:
        if type(child_name) is not bytes or type(child_digest) is not bytes:
            raise RuntimeFdTreeSnapshotError("已完成对象访问修复子树身份无效")
        _update_digest(digest, child_name)
        _update_digest(digest, child_digest)
    return digest.digest()


def _require_snapshot_fields(
    *,
    identity_digest: bytes,
    entries: int,
    total_bytes: int,
    root_device: int,
    root_inode: int,
) -> None:
    if (
        type(identity_digest) is not bytes
        or len(identity_digest) != hashlib.sha256().digest_size
        or type(entries) is not int
        or entries <= 0
        or type(total_bytes) is not int
        or total_bytes < 0
        or type(root_device) is not int
        or root_device < 0
        or type(root_inode) is not int
        or root_inode <= 0
    ):
        raise RuntimeFdTreeSnapshotError("运行时对象身份快照无效")


def _update_digest(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, byteorder="big", signed=False))
    digest.update(value)


__all__ = [
    "RuntimeFdTreeAccessRepairSnapshot",
    "RuntimeFdTreeSnapshot",
    "RuntimeFdTreeSnapshotError",
    "access_repair_entry_snapshot_digest",
    "encode_snapshot_name",
    "entry_snapshot_digest",
    "new_access_repair_snapshot",
    "new_identity_snapshot",
    "require_access_repair_snapshot",
    "require_identity_snapshot",
]
