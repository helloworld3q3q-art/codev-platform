"""Linux 运行观测产物命名空间的所有权迁移边界。"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from dataclasses import astuple, dataclass
from pathlib import Path

from codev_platform._runtime_artifact_descriptor_path import (
    RuntimeArtifactDescriptorPathError,
    bind_runtime_artifact_path,
    require_descriptor_without_acl as _require_no_acl,
)
from codev_platform.core.runtime_artifacts import RuntimeArtifactLayout


_NAMESPACE_NAMES = ("logs", "audit", "mcp_serve_logs", "platform_meta", "run")
_DIRECTORY_MODE = 0o700
_REGULAR_MODE = 0o600
_DEFAULT_MAX_ENTRIES = 100_000
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


class RuntimeArtifactAccessError(RuntimeError):
    """运行观测产物无法在可信边界内完成权限迁移。"""


@dataclass(frozen=True, slots=True)
class RuntimeArtifactAccessProof:
    schema_version: int
    namespace_count: int
    entry_count: int
    changed_entry_count: int
    target_uid: int
    target_gid: int
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class _AccessPolicy:
    service_uid: int
    service_gid: int
    max_entries: int

    def __post_init__(self) -> None:
        for label, value in (("服务 UID", self.service_uid), ("服务 GID", self.service_gid)):
            if type(value) is not int or not 0 < value < (1 << 32) - 1:
                raise ValueError(f"{label} 无效")
        if type(self.max_entries) is not int:
            raise TypeError("运行观测产物条目预算类型无效")
        if not 1 <= self.max_entries <= 1_000_000:
            raise ValueError("运行观测产物条目预算超出安全范围")


@dataclass(frozen=True, slots=True)
class _EntryIdentity:
    device: int
    inode: int
    kind: int
    links: int
    uid: int
    gid: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int

    def content_key(self) -> tuple[int, ...]:
        """排除本操作会改变的 owner、mode 与 ctime。"""
        values = astuple(self)
        return values[:4] + values[7:9]


@dataclass(frozen=True, slots=True)
class _NodeSnapshot:
    name: str
    identity: _EntryIdentity
    children: tuple[_NodeSnapshot, ...] = ()


@dataclass(slots=True)
class _TraversalBudget:
    limit: int
    entries: int = 0

    def add(self) -> None:
        self.entries += 1
        if self.entries > self.limit:
            raise RuntimeArtifactAccessError("运行观测产物超过条目预算")


def migrate_runtime_artifact_access(
    layout: RuntimeArtifactLayout,
    *,
    service_uid: int,
    service_gid: int,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
) -> RuntimeArtifactAccessProof:
    """把固定观测命名空间安全收敛为目标服务用户私有。"""
    _require_linux_root()
    try:
        policy = _AccessPolicy(service_uid, service_gid, max_entries)
        root = _normalize_root(layout)
        with bind_runtime_artifact_path(
            root,
            service_uid=policy.service_uid,
            service_gid=policy.service_gid,
        ) as binding:
            root_fd = binding.root_fd
            root_device = _validate_root_descriptor(root_fd, policy)
            _prepare_namespaces(root_fd, root_device, policy)
            root_identity = _raw_identity(os.fstat(root_fd))
            first = _scan_namespaces(root_fd, root_device, policy)
            second = _scan_namespaces(root_fd, root_device, policy)
            if first != second:
                raise RuntimeArtifactAccessError("运行观测产物两遍预检发生漂移")
            changed = sum(
                _publish_node(
                    root_fd,
                    snapshot,
                    root_device=root_device,
                    policy=policy,
                )
                for snapshot in second
            )
            final = _scan_namespaces(
                root_fd,
                root_device,
                policy,
                require_canonical=True,
            )
            if _content_tree(second) != _content_tree(final):
                raise RuntimeArtifactAccessError("运行观测产物发布后内容身份发生漂移")
            opened_root = _entry_identity(os.fstat(root_fd), root_device=root_device, policy=policy)
            if root_identity != opened_root:
                raise RuntimeArtifactAccessError("运行观测产物数据根身份复验失败")
            _require_no_acl(root_fd)
            binding.verify_visible()
            visible_root = _entry_identity(
                os.fstat(binding.verified_root_fd),
                root_device=root_device,
                policy=policy,
            )
            if opened_root != visible_root:
                raise RuntimeArtifactAccessError("运行观测产物数据根身份复验失败")
            proof = RuntimeArtifactAccessProof(
                schema_version=1,
                namespace_count=len(_NAMESPACE_NAMES),
                entry_count=_count_nodes(final),
                changed_entry_count=changed,
                target_uid=service_uid,
                target_gid=service_gid,
                evidence_sha256=_evidence_digest(final, policy),
            )
            binding.confirm_visible()
            return proof
    except RuntimeArtifactDescriptorPathError as exc:
        raise RuntimeArtifactAccessError(str(exc)) from None
    except RuntimeArtifactAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeArtifactAccessError("运行观测产物权限迁移失败") from None


def _require_linux_root() -> None:
    geteuid = getattr(os, "geteuid", None)
    if not sys.platform.startswith("linux") or geteuid is None or geteuid() != 0:
        raise RuntimeArtifactAccessError("运行观测产物迁移仅允许 Linux root 执行")


def _normalize_root(layout: RuntimeArtifactLayout) -> Path:
    if not isinstance(layout, RuntimeArtifactLayout):
        raise TypeError("运行观测产物布局类型无效")
    root = Path(layout.root)
    if not root.is_absolute() or ".." in root.parts or root.parent == root:
        raise RuntimeArtifactAccessError("运行观测产物数据根无效")
    return root


def _validate_root_descriptor(descriptor: int, policy: _AccessPolicy) -> int:
    try:
        opened = os.fstat(descriptor)
    except OSError:
        raise RuntimeArtifactAccessError("运行观测产物数据根身份复验失败") from None
    identity = _entry_identity(opened, root_device=opened.st_dev, policy=policy)
    if not stat.S_ISDIR(identity.kind):
        raise RuntimeArtifactAccessError("运行观测产物数据根不是目录")
    _require_service_root_execute(identity, policy)
    _require_no_acl(descriptor)
    return int(opened.st_dev)


def _prepare_namespaces(root_fd: int, root_device: int, policy: _AccessPolicy) -> None:
    created = False
    for name in _NAMESPACE_NAMES:
        before = _stat_at(root_fd, name, missing_ok=True)
        if before is None:
            try:
                os.mkdir(name, _DIRECTORY_MODE, dir_fd=root_fd)
                created = True
            except FileExistsError:
                pass
            except OSError:
                raise RuntimeArtifactAccessError("运行观测产物命名空间无法安全创建") from None
        metadata = _stat_at(root_fd, name)
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeArtifactAccessError("运行观测产物禁止符号链接")
        descriptor = _open_entry(name, stat.S_IFDIR, dir_fd=root_fd)
        try:
            opened = _entry_identity(os.fstat(descriptor), root_device=root_device, policy=policy)
            if _raw_identity(metadata) != opened:
                raise RuntimeArtifactAccessError("运行观测产物命名空间身份复验失败")
            _require_no_acl(descriptor)
        finally:
            os.close(descriptor)
    if created:
        try:
            os.fsync(root_fd)
        except OSError:
            raise RuntimeArtifactAccessError("运行观测产物命名空间无法持久化") from None


def _scan_namespaces(
    root_fd: int,
    root_device: int,
    policy: _AccessPolicy,
    *,
    require_canonical: bool = False,
) -> tuple[_NodeSnapshot, ...]:
    budget = _TraversalBudget(policy.max_entries)
    return tuple(
        _scan_node(
            root_fd,
            name,
            root_device=root_device,
            policy=policy,
            budget=budget,
            depth=0,
            require_canonical=require_canonical,
        )
        for name in _NAMESPACE_NAMES
    )


def _scan_node(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
    policy: _AccessPolicy,
    budget: _TraversalBudget,
    depth: int,
    require_canonical: bool,
) -> _NodeSnapshot:
    if depth > 64:
        raise RuntimeArtifactAccessError("运行观测产物目录深度超过安全范围")
    budget.add()
    before = _stat_at(parent_fd, name)
    if stat.S_ISLNK(before.st_mode):
        raise RuntimeArtifactAccessError("运行观测产物禁止符号链接")
    expected = _entry_identity(before, root_device=root_device, policy=policy)
    descriptor = _open_entry(name, expected.kind, dir_fd=parent_fd)
    try:
        opened = _entry_identity(
            os.fstat(descriptor),
            root_device=root_device,
            policy=policy,
        )
        _require_same_identity(expected, opened)
        _require_no_acl(descriptor)
        if require_canonical:
            _require_canonical(opened, policy)
        names = () if stat.S_ISREG(opened.kind) else _directory_names(descriptor)
        children = tuple(
            _scan_node(
                descriptor,
                child_name,
                root_device=root_device,
                policy=policy,
                budget=budget,
                depth=depth + 1,
                require_canonical=require_canonical,
            )
            for child_name in names
        )
        final = _entry_identity(
            os.fstat(descriptor),
            root_device=root_device,
            policy=policy,
        )
        _require_same_identity(opened, final)
        linked = _entry_identity(
            _stat_at(parent_fd, name),
            root_device=root_device,
            policy=policy,
        )
        _require_same_identity(final, linked)
        return _NodeSnapshot(name=name, identity=final, children=children)
    finally:
        os.close(descriptor)


def _publish_node(
    parent_fd: int,
    snapshot: _NodeSnapshot,
    *,
    root_device: int,
    policy: _AccessPolicy,
) -> int:
    before = _entry_identity(
        _stat_at(parent_fd, snapshot.name),
        root_device=root_device,
        policy=policy,
    )
    _require_same_identity(snapshot.identity, before)
    descriptor = _open_entry(snapshot.name, before.kind, dir_fd=parent_fd)
    try:
        opened = _entry_identity(
            os.fstat(descriptor),
            root_device=root_device,
            policy=policy,
        )
        _require_same_identity(snapshot.identity, opened)
        _require_no_acl(descriptor)
        changed = 0
        if stat.S_ISDIR(snapshot.identity.kind):
            expected_names = tuple(child.name for child in snapshot.children)
            if _directory_names(descriptor) != expected_names:
                raise RuntimeArtifactAccessError("运行观测产物目录身份复验失败")
            changed = sum(
                _publish_node(
                    descriptor,
                    child,
                    root_device=root_device,
                    policy=policy,
                )
                for child in snapshot.children
            )
            if _directory_names(descriptor) != expected_names:
                raise RuntimeArtifactAccessError("运行观测产物目录身份复验失败")
        changed += int(_converge_opened(descriptor, snapshot.identity, policy))
        final = _entry_identity(
            os.fstat(descriptor),
            root_device=root_device,
            policy=policy,
        )
        _require_canonical(final, policy)
        linked = _entry_identity(
            _stat_at(parent_fd, snapshot.name),
            root_device=root_device,
            policy=policy,
        )
        _require_same_identity(final, linked)
        return changed
    finally:
        os.close(descriptor)


def _converge_opened(
    descriptor: int,
    expected: _EntryIdentity,
    policy: _AccessPolicy,
) -> bool:
    current = _raw_identity(os.fstat(descriptor))
    _require_same_identity(expected, current)
    mode = _canonical_mode(current.kind)
    changed = (
        current.uid != policy.service_uid
        or current.gid != policy.service_gid
        or current.mode != mode
    )
    try:
        if current.uid != policy.service_uid or current.gid != policy.service_gid:
            os.fchown(descriptor, policy.service_uid, policy.service_gid)
        if current.mode != mode:
            os.fchmod(descriptor, mode)
        if changed:
            os.fsync(descriptor)
    except OSError:
        raise RuntimeArtifactAccessError("运行观测产物所有权无法安全提交") from None
    final = _raw_identity(os.fstat(descriptor))
    if current.content_key() != final.content_key():
        raise RuntimeArtifactAccessError("运行观测产物提交时内容身份发生漂移")
    _require_canonical(final, policy)
    _require_no_acl(descriptor)
    return changed


def _entry_identity(
    metadata,
    *,
    root_device: int,
    policy: _AccessPolicy,
) -> _EntryIdentity:
    identity = _raw_identity(metadata)
    if stat.S_ISLNK(identity.kind):
        raise RuntimeArtifactAccessError("运行观测产物禁止符号链接")
    if not stat.S_ISDIR(identity.kind) and not stat.S_ISREG(identity.kind):
        raise RuntimeArtifactAccessError("运行观测产物包含特殊文件")
    if identity.device != root_device:
        raise RuntimeArtifactAccessError("运行观测产物禁止跨设备对象")
    if stat.S_ISREG(identity.kind) and identity.links != 1:
        raise RuntimeArtifactAccessError("运行观测产物禁止普通文件硬链接")
    if identity.uid not in {0, policy.service_uid} or identity.gid not in {
        0,
        policy.service_gid,
    }:
        raise RuntimeArtifactAccessError("运行观测产物所有者不受信任")
    if identity.mode & 0o022:
        raise RuntimeArtifactAccessError("运行观测产物禁止组或全局可写")
    if identity.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise RuntimeArtifactAccessError("运行观测产物包含特殊权限位")
    return identity


def _raw_identity(metadata) -> _EntryIdentity:
    return _EntryIdentity(
        device=int(metadata.st_dev),
        inode=int(metadata.st_ino),
        kind=stat.S_IFMT(metadata.st_mode),
        links=int(metadata.st_nlink),
        uid=int(metadata.st_uid),
        gid=int(metadata.st_gid),
        mode=stat.S_IMODE(metadata.st_mode),
        size=int(metadata.st_size),
        modified_ns=int(metadata.st_mtime_ns),
        changed_ns=int(metadata.st_ctime_ns),
    )


def _require_same_identity(expected: _EntryIdentity, current: _EntryIdentity) -> None:
    if expected != current:
        raise RuntimeArtifactAccessError("运行观测产物身份复验失败")


def _require_canonical(identity: _EntryIdentity, policy: _AccessPolicy) -> None:
    if (identity.uid, identity.gid) != (policy.service_uid, policy.service_gid):
        raise RuntimeArtifactAccessError("运行观测产物目标所有权未提交")
    if identity.mode != _canonical_mode(identity.kind):
        raise RuntimeArtifactAccessError("运行观测产物目标权限未提交")


def _require_service_root_execute(identity: _EntryIdentity, policy: _AccessPolicy) -> None:
    required = _service_execute_bit(identity.uid, identity.gid, policy)
    if not identity.mode & required:
        raise RuntimeArtifactAccessError("运行观测产物服务用户无法穿越数据根")


def _service_execute_bit(uid: int, gid: int, policy: _AccessPolicy) -> int:
    if uid == policy.service_uid:
        return stat.S_IXUSR
    if gid == policy.service_gid:
        return stat.S_IXGRP
    return stat.S_IXOTH


def _canonical_mode(kind: int) -> int:
    return _DIRECTORY_MODE if stat.S_ISDIR(kind) else _REGULAR_MODE


def _stat_at(parent_fd: int, name: str, *, missing_ok: bool = False):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise RuntimeArtifactAccessError("运行观测产物目录项缺失") from None
    except OSError:
        raise RuntimeArtifactAccessError("运行观测产物目录项无法安全检查") from None


def _open_entry(name: str, kind: int, *, dir_fd: int) -> int:
    if stat.S_ISDIR(kind):
        flags = _DIRECTORY_OPEN_FLAGS
    else:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError:
        raise RuntimeArtifactAccessError("运行观测产物目录项无法安全打开") from None


def _directory_names(descriptor: int) -> tuple[str, ...]:
    try:
        with os.scandir(descriptor) as entries:
            names = tuple(sorted(entry.name for entry in entries))
    except OSError:
        raise RuntimeArtifactAccessError("运行观测产物目录无法完整枚举") from None
    if any(not name or name in {".", ".."} or "/" in name for name in names):
        raise RuntimeArtifactAccessError("运行观测产物目录项名称无效")
    return names


def _content_tree(nodes: tuple[_NodeSnapshot, ...]) -> tuple:
    return tuple(
        (node.name, node.identity.content_key(), _content_tree(node.children)) for node in nodes
    )


def _count_nodes(nodes: tuple[_NodeSnapshot, ...]) -> int:
    return sum(1 + _count_nodes(node.children) for node in nodes)


def _evidence_digest(
    nodes: tuple[_NodeSnapshot, ...],
    policy: _AccessPolicy,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"runtime-artifact-access-v1\0")
    digest.update(f"{policy.service_uid}:{policy.service_gid}\0".encode("ascii"))
    _update_evidence(digest, nodes)
    return digest.hexdigest()


def _update_evidence(digest, nodes: tuple[_NodeSnapshot, ...]) -> None:
    digest.update(f"[{len(nodes)}]".encode("ascii"))
    for node in nodes:
        encoded_name = os.fsencode(node.name)
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        values = astuple(node.identity)
        digest.update((":".join(str(value) for value in values) + "\0").encode("ascii"))
        _update_evidence(digest, node.children)


__all__ = [
    "RuntimeArtifactAccessError",
    "RuntimeArtifactAccessProof",
    "migrate_runtime_artifact_access",
]
