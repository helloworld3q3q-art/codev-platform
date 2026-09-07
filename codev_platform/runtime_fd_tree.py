"""运行时对象树的 fd-relative 安全遍历内核。"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from codev_platform._runtime_fd_tree_contracts import (
    CanonicalizeMode as CanonicalizeMode,
    ConvergeCompletedAccessRepair as ConvergeCompletedAccessRepair,
    PreflightGroup as PreflightGroup,
    PreflightCompletedAccessRepair as PreflightCompletedAccessRepair,
    PublishGroup as PublishGroup,
    RuntimeFdGroupOperation as RuntimeFdGroupOperation,
    RuntimeFdGroupPolicy as RuntimeFdGroupPolicy,
    RuntimeFdModePolicy as RuntimeFdModePolicy,
    RuntimeFdTreeError as RuntimeFdTreeError,
    RuntimeFdTreeOperation as RuntimeFdTreeOperation,
    RuntimeFdTreeReport as RuntimeFdTreeReport,
    RuntimeRootMarkerPolicy as RuntimeRootMarkerPolicy,
    VerifyGroup as VerifyGroup,
    VerifyOnly as VerifyOnly,
    _require_group_operation_policies as _require_group_operation_policies,
    _require_identity_snapshot as _require_identity_snapshot,
    _require_operation_policies as _require_operation_policies,
)
from codev_platform._runtime_fd_tree_io import (
    _normalize_root as _normalize_root,
    _open_directory as _open_directory,
    _open_regular as _open_regular,
    _read_bounded as _read_bounded,
    _stat_at as _stat_at,
)
from codev_platform._runtime_fd_tree_snapshot import (
    RuntimeFdTreeAccessRepairSnapshot,
    RuntimeFdTreeSnapshot,
    RuntimeFdTreeSnapshotError,
    access_repair_entry_snapshot_digest as _access_repair_entry_snapshot_digest,
    encode_snapshot_name as _encode_name,
    entry_snapshot_digest as _entry_snapshot_digest,
    new_access_repair_snapshot as _new_access_repair_snapshot,
    new_identity_snapshot as _new_identity_snapshot,
)
from codev_platform._runtime_fd_tree_validation import (
    _content_identity as _content_identity,
    _entry_identity as _entry_identity,
    _group_content_identity as _group_content_identity,
    _group_requires_target as _group_requires_target,
    _require_linux_root as _require_linux_root,
    _require_no_extended_attributes as _require_no_extended_attributes,
    _require_same_entry as _require_same_entry,
    _require_stable_content as _require_stable_content,
    _require_stable_group_content as _require_stable_group_content,
    _validate_completed_access_repair_metadata as _validate_completed_access_repair_metadata,
    _validate_entry_metadata as _validate_entry_metadata,
    _validate_group_metadata as _validate_group_metadata,
)


_MAX_TREE_ENTRIES = 2_000_000
_MAX_TREE_BYTES = 256 * 1024 * 1024 * 1024
_MAX_TREE_DEPTH = 256
_PROGRESS_ENTRY_INTERVAL = 10_000
_LOGGER = logging.getLogger(__name__)
_SUPPORTED_OPERATION_TYPES = frozenset(
    {
        VerifyOnly,
        CanonicalizeMode,
        PreflightCompletedAccessRepair,
        ConvergeCompletedAccessRepair,
        PreflightGroup,
        PublishGroup,
        VerifyGroup,
    }
)
_COMPLETED_ACCESS_REPAIR_OPERATION_TYPES = frozenset(
    {
        PreflightCompletedAccessRepair,
        ConvergeCompletedAccessRepair,
    }
)


@dataclass(slots=True)
class _TraversalBudget:
    entries: int = 0
    total_bytes: int = 0
    next_progress: int = _PROGRESS_ENTRY_INTERVAL

    def add(self, *, regular_size: int) -> None:
        self.entries, self.total_bytes = _advance_budget(
            self.entries,
            self.total_bytes,
            regular_size=regular_size,
        )
        if self.entries >= self.next_progress:
            _LOGGER.info(
                "运行时对象安全遍历进度：条目=%d，字节=%d",
                self.entries,
                self.total_bytes,
            )
            while self.next_progress <= self.entries:
                self.next_progress += _PROGRESS_ENTRY_INTERVAL


def walk_runtime_tree(root: Path, operation: RuntimeFdTreeOperation) -> RuntimeFdTreeReport:
    """按封闭操作遍历对象树；拒绝调用方注入任意高权限逻辑。"""
    _require_supported_operation(operation)
    _require_linux_root()
    if type(operation) is ConvergeCompletedAccessRepair:
        _require_access_repair_preflight(root, operation, from_cwd=False)
    return _access_tree(Path(root), operation)


def walk_runtime_tree_from_cwd(
    root: Path,
    operation: RuntimeFdTreeOperation,
) -> RuntimeFdTreeReport:
    """仅供 root-fd worker 在已 ``fchdir`` 的可信 cwd 内遍历相对对象根。"""
    _require_supported_operation(operation)
    _require_linux_root()
    if type(operation) is ConvergeCompletedAccessRepair:
        _require_access_repair_preflight(root, operation, from_cwd=True)
    return _access_tree_from_cwd(root, operation)


def _require_supported_operation(operation: RuntimeFdTreeOperation) -> None:
    if type(operation) not in _SUPPORTED_OPERATION_TYPES:
        raise RuntimeFdTreeError("运行时对象树操作无效")


def _require_access_repair_preflight(
    root: Path,
    operation: ConvergeCompletedAccessRepair,
    *,
    from_cwd: bool,
) -> None:
    preflight = PreflightCompletedAccessRepair(
        mode_policy=operation.mode_policy,
        marker_policy=operation.marker_policy,
    )
    report = (
        _access_tree_from_cwd(root, preflight)
        if from_cwd
        else _access_tree(Path(root), preflight)
    )
    if report.access_repair_snapshot != operation.expected_snapshot:
        raise RuntimeFdTreeError("已完成对象访问修复预检快照发生漂移")


def _is_completed_access_repair(operation: RuntimeFdTreeOperation) -> bool:
    return type(operation) in _COMPLETED_ACCESS_REPAIR_OPERATION_TYPES


def _validate_runtime_entry(
    metadata: os.stat_result,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    publish_incomplete: bool | None,
) -> None:
    if _is_completed_access_repair(operation):
        _validate_completed_access_repair_metadata(
            metadata,
            root_device=root_device,
            operation=operation,
        )
    else:
        _validate_entry_metadata(
            metadata,
            root_device=root_device,
            require_canonical=type(operation) is not CanonicalizeMode,
            mode_policy=operation.mode_policy,
        )
    if type(operation) in {PreflightGroup, PublishGroup, VerifyGroup}:
        _validate_group_metadata(
            metadata,
            operation,
            require_target=_group_requires_target(operation, publish_incomplete),
        )


def _cwd_relative_root(value: Path) -> Path:
    root = Path(value)
    if root.is_absolute() or not root.parts:
        raise RuntimeFdTreeError("worker 对象根必须是安全相对路径")
    if any(part in {"", ".", ".."} or "\x00" in part for part in root.parts):
        raise RuntimeFdTreeError("worker 对象根必须是安全相对路径")
    return root


def _advance_budget(
    entry_count: int,
    total_bytes: int,
    *,
    regular_size: int,
) -> tuple[int, int]:
    entries = entry_count + 1
    size = total_bytes + regular_size
    if entries > _MAX_TREE_ENTRIES:
        raise RuntimeFdTreeError("运行时对象树条目超过安全预算")
    if size > _MAX_TREE_BYTES:
        raise RuntimeFdTreeError("运行时对象树大小超过安全预算")
    return entries, size


def _access_tree(root: Path, operation: RuntimeFdTreeOperation) -> RuntimeFdTreeReport:
    return _access_normalized_tree(_normalize_root(root), operation)


def _access_tree_from_cwd(root: Path, operation: RuntimeFdTreeOperation) -> RuntimeFdTreeReport:
    return _access_normalized_tree(_cwd_relative_root(root), operation)


def _access_normalized_tree(
    path: Path,
    operation: RuntimeFdTreeOperation,
) -> RuntimeFdTreeReport:
    descriptor = -1
    try:
        linked = path.lstat()
        descriptor = _open_directory(path)
        opened = os.fstat(descriptor)
        _require_same_entry(linked, opened, label="对象根目录")
        root_device = opened.st_dev
        publish_incomplete = opened.st_gid == 0 if type(operation) is PublishGroup else None
        _validate_runtime_entry(
            opened,
            root_device=root_device,
            operation=operation,
            publish_incomplete=publish_incomplete,
        )
        _require_no_extended_attributes(descriptor)
        marker_before = _inspect_marker(
            descriptor,
            root_device=root_device,
            operation=operation,
            require_canonical=type(operation) is not CanonicalizeMode,
        )
        budget = _TraversalBudget()
        budget.add(regular_size=0)
        identity_digest, access_repair_digest = _visit_directory(
            descriptor,
            opened,
            name="",
            root_device=root_device,
            operation=operation,
            budget=budget,
            depth=0,
            publish_incomplete=publish_incomplete,
            is_root=True,
        )
        final = os.fstat(descriptor)
        _require_same_entry(final, path.lstat(), label="对象根目录")
        marker_after = _inspect_marker(
            descriptor,
            root_device=root_device,
            operation=operation,
            require_canonical=True,
        )
        if marker_after != marker_before:
            raise RuntimeFdTreeError("运行时对象未完成标记发生漂移")
        snapshot = _new_identity_snapshot(
            identity_digest=identity_digest,
            entries=budget.entries,
            total_bytes=budget.total_bytes,
            root_device=final.st_dev,
            root_inode=final.st_ino,
        )
        access_repair_snapshot = None
        if access_repair_digest is not None:
            access_repair_snapshot = _new_access_repair_snapshot(
                identity_digest=access_repair_digest,
                entries=budget.entries,
                total_bytes=budget.total_bytes,
                root_device=final.st_dev,
                root_inode=final.st_ino,
            )
            if (
                type(operation) is ConvergeCompletedAccessRepair
                and access_repair_snapshot != operation.expected_snapshot
            ):
                raise RuntimeFdTreeError("已完成对象访问修复快照发生漂移")
        if type(operation) in {PublishGroup, VerifyGroup}:
            if snapshot != operation.expected_snapshot:
                raise RuntimeFdTreeError("运行时对象树身份快照发生漂移")
        return RuntimeFdTreeReport(
            entries=budget.entries,
            total_bytes=budget.total_bytes,
            root_device=final.st_dev,
            root_inode=final.st_ino,
            identity_snapshot=snapshot,
            access_repair_snapshot=access_repair_snapshot,
        )
    except RuntimeFdTreeError:
        raise
    except (OSError, TypeError, ValueError, RuntimeFdTreeSnapshotError):
        raise RuntimeFdTreeError("运行时对象树无法安全遍历") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _visit_directory(
    descriptor: int,
    metadata: os.stat_result,
    *,
    name: str,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    budget: _TraversalBudget,
    depth: int,
    publish_incomplete: bool | None,
    is_root: bool,
) -> tuple[bytes, bytes | None]:
    if depth > _MAX_TREE_DEPTH:
        raise RuntimeFdTreeError("运行时对象树深度超过安全预算")
    current = _prepare_opened_entry(
        descriptor,
        metadata,
        root_device=root_device,
        operation=operation,
        publish_incomplete=publish_incomplete,
    )
    child_digests: list[tuple[bytes, bytes]] = []
    access_repair_child_digests: list[tuple[bytes, bytes]] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                child_digest, access_repair_child_digest = _visit_child(
                    descriptor,
                    entry.name,
                    root_device=root_device,
                    operation=operation,
                    budget=budget,
                    depth=depth + 1,
                    publish_incomplete=publish_incomplete,
                )
                encoded_name = _encode_name(entry.name)
                child_digests.append((encoded_name, child_digest))
                if access_repair_child_digest is not None:
                    access_repair_child_digests.append(
                        (encoded_name, access_repair_child_digest)
                    )
    except RuntimeFdTreeError:
        raise
    except OSError:
        raise RuntimeFdTreeError("运行时目录无法完整枚举") from None
    if type(operation) is PublishGroup:
        current = _publish_opened_entry(
            descriptor,
            current,
            operation,
            fsync_existing=bool(publish_incomplete),
            is_root=is_root,
        )
    if type(operation) is ConvergeCompletedAccessRepair:
        current = _converge_completed_access_entry(
            descriptor,
            current,
            root_device=root_device,
            operation=operation,
        )
    _require_same_entry(current, os.fstat(descriptor), label="运行时目录")
    identity_digest = _entry_snapshot_digest(
        name,
        current,
        child_digests=tuple(sorted(child_digests)),
    )
    access_repair_digest = None
    if _is_completed_access_repair(operation):
        access_repair_digest = _access_repair_entry_snapshot_digest(
            name,
            current,
            child_digests=tuple(sorted(access_repair_child_digests)),
        )
    return identity_digest, access_repair_digest


def _visit_child(
    parent_fd: int,
    name: str,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    budget: _TraversalBudget,
    depth: int,
    publish_incomplete: bool | None,
) -> tuple[bytes, bytes | None]:
    if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
        raise RuntimeFdTreeError("运行时对象目录项名称无效")
    before = _stat_at(parent_fd, name)
    regular_size = before.st_size if stat.S_ISREG(before.st_mode) else 0
    budget.add(regular_size=regular_size)
    _validate_runtime_entry(
        before,
        root_device=root_device,
        operation=operation,
        publish_incomplete=publish_incomplete,
    )
    if stat.S_ISLNK(before.st_mode):
        linked = _verify_symlink(parent_fd, name, before)
        identity_digest = _entry_snapshot_digest(name, linked)
        access_repair_digest = None
        if _is_completed_access_repair(operation):
            access_repair_digest = _access_repair_entry_snapshot_digest(name, linked)
        return identity_digest, access_repair_digest
    if stat.S_ISDIR(before.st_mode):
        return _visit_child_directory(
            parent_fd,
            name,
            before,
            root_device=root_device,
            operation=operation,
            budget=budget,
            depth=depth,
            publish_incomplete=publish_incomplete,
        )
    return _visit_regular_file(
        parent_fd,
        name,
        before,
        root_device=root_device,
        operation=operation,
        publish_incomplete=publish_incomplete,
    )


def _visit_child_directory(
    parent_fd: int,
    name: str,
    before: os.stat_result,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    budget: _TraversalBudget,
    depth: int,
    publish_incomplete: bool | None,
) -> tuple[bytes, bytes | None]:
    descriptor = _open_directory(name, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        _require_same_entry(before, opened, label="运行时目录")
        digest = _visit_directory(
            descriptor,
            opened,
            name=name,
            root_device=root_device,
            operation=operation,
            budget=budget,
            depth=depth,
            publish_incomplete=publish_incomplete,
            is_root=False,
        )
        _require_same_entry(
            os.fstat(descriptor),
            _stat_at(parent_fd, name),
            label="运行时目录",
        )
        return digest
    finally:
        os.close(descriptor)


def _visit_regular_file(
    parent_fd: int,
    name: str,
    before: os.stat_result,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    publish_incomplete: bool | None,
) -> tuple[bytes, bytes | None]:
    descriptor = _open_regular(name, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        _require_same_entry(before, opened, label="运行时普通文件")
        current = _prepare_opened_entry(
            descriptor,
            opened,
            root_device=root_device,
            operation=operation,
            publish_incomplete=publish_incomplete,
        )
        if type(operation) is PublishGroup:
            current = _publish_opened_entry(
                descriptor,
                current,
                operation,
                fsync_existing=bool(publish_incomplete),
                is_root=False,
            )
        _require_same_entry(
            current,
            _stat_at(parent_fd, name),
            label="运行时普通文件",
        )
        identity_digest = _entry_snapshot_digest(name, current)
        access_repair_digest = None
        if _is_completed_access_repair(operation):
            access_repair_digest = _access_repair_entry_snapshot_digest(name, current)
        return identity_digest, access_repair_digest
    finally:
        os.close(descriptor)


def _prepare_opened_entry(
    descriptor: int,
    metadata: os.stat_result,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    publish_incomplete: bool | None = None,
) -> os.stat_result:
    seal = type(operation) is CanonicalizeMode
    _validate_runtime_entry(
        metadata,
        root_device=root_device,
        operation=operation,
        publish_incomplete=publish_incomplete,
    )
    _require_no_extended_attributes(descriptor)
    expected = operation.mode_policy.canonical_mode(metadata.st_mode)
    if seal and stat.S_IMODE(metadata.st_mode) != expected:
        try:
            os.fchmod(descriptor, expected)
        except OSError:
            raise RuntimeFdTreeError("运行时对象访问模式无法安全定型") from None
    current = os.fstat(descriptor)
    if seal:
        _require_stable_content(metadata, current)
    else:
        _require_same_entry(metadata, current, label="运行时对象")
    _validate_runtime_entry(
        current,
        root_device=root_device,
        operation=operation,
        publish_incomplete=publish_incomplete,
    )
    _require_no_extended_attributes(descriptor)
    return current


def _converge_completed_access_entry(
    descriptor: int,
    metadata: os.stat_result,
    *,
    root_device: int,
    operation: ConvergeCompletedAccessRepair,
) -> os.stat_result:
    """收敛一个已预检目录，并让中断后的重试补齐持久化。"""
    if type(operation) is not ConvergeCompletedAccessRepair:
        raise RuntimeFdTreeError("已完成对象访问修复操作无效")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeFdTreeError("已完成对象访问修复只能收敛目录")
    current = os.fstat(descriptor)
    _require_same_entry(metadata, current, label="运行时目录")
    _validate_completed_access_repair_metadata(
        current,
        root_device=root_device,
        operation=operation,
    )
    _require_no_extended_attributes(descriptor)
    expected_mode = operation.mode_policy.canonical_mode(current.st_mode)
    changed = stat.S_IMODE(current.st_mode) != expected_mode
    try:
        if changed:
            os.fchmod(descriptor, expected_mode)
        os.fsync(descriptor)
    except OSError:
        raise RuntimeFdTreeError("已完成对象访问模式无法安全收敛") from None
    final = os.fstat(descriptor)
    if changed:
        _require_stable_content(current, final)
    else:
        _require_same_entry(current, final, label="运行时目录")
    _validate_completed_access_repair_metadata(
        final,
        root_device=root_device,
        operation=operation,
    )
    if stat.S_IMODE(final.st_mode) != expected_mode:
        raise RuntimeFdTreeError("已完成对象访问模式无法安全收敛")
    _require_no_extended_attributes(descriptor)
    return final


def _publish_opened_entry(
    descriptor: int,
    metadata: os.stat_result,
    operation: PublishGroup,
    *,
    fsync_existing: bool,
    is_root: bool,
) -> os.stat_result:
    """发布一个已预检的打开对象，并在返回前持久化和复验。"""
    if type(operation) is not PublishGroup:
        raise RuntimeFdTreeError("运行时对象树 GID 发布操作无效")
    current = os.fstat(descriptor)
    _require_same_entry(metadata, current, label="运行时对象")
    metadata = current
    _validate_entry_metadata(
        metadata,
        root_device=metadata.st_dev,
        require_canonical=True,
        mode_policy=operation.mode_policy,
    )
    _validate_group_metadata(metadata, operation, require_target=False)
    _require_no_extended_attributes(descriptor)
    if type(fsync_existing) is not bool or type(is_root) is not bool:
        raise RuntimeFdTreeError("运行时对象树 GID 持久化策略无效")
    changed = metadata.st_gid != operation.group_policy.target_gid
    try:
        if changed:
            os.fchown(descriptor, -1, operation.group_policy.target_gid)
        if changed or fsync_existing or is_root:
            os.fsync(descriptor)
    except OSError:
        raise RuntimeFdTreeError("运行时对象服务组无法安全发布") from None
    current = os.fstat(descriptor)
    if changed:
        _require_stable_group_content(metadata, current)
    else:
        _require_same_entry(metadata, current, label="运行时对象")
    _validate_entry_metadata(
        current,
        root_device=metadata.st_dev,
        require_canonical=True,
        mode_policy=operation.mode_policy,
    )
    _validate_group_metadata(current, operation, require_target=True)
    _require_no_extended_attributes(descriptor)
    return current


def _verify_symlink(
    parent_fd: int,
    name: str,
    before: os.stat_result,
) -> os.stat_result:
    proc_path = f"/proc/self/fd/{parent_fd}/{name}"
    try:
        attributes = os.listxattr(proc_path, follow_symlinks=False)
    except (AttributeError, OSError):
        raise RuntimeFdTreeError("运行时链接扩展属性或 ACL 无法复验") from None
    if attributes:
        raise RuntimeFdTreeError("运行时对象包含扩展属性或 ACL")
    after = _stat_at(parent_fd, name)
    _require_same_entry(before, after, label="运行时符号链接")
    return after


def _inspect_marker(
    root_fd: int,
    *,
    root_device: int,
    operation: RuntimeFdTreeOperation,
    require_canonical: bool,
) -> tuple[int, int, bytes] | None:
    policy = operation.marker_policy
    before = _stat_at(root_fd, policy.name, missing_ok=True)
    if before is None:
        if operation.require_marker:
            raise RuntimeFdTreeError("运行时对象缺少可信未完成标记")
        return None
    if operation.forbid_marker:
        raise RuntimeFdTreeError("运行时对象仍带有未完成标记")
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeFdTreeError("运行时对象未完成标记不受信任")
    _validate_entry_metadata(
        before,
        root_device=root_device,
        require_canonical=require_canonical,
        mode_policy=operation.mode_policy,
    )
    descriptor = _open_regular(policy.name, dir_fd=root_fd)
    try:
        opened = os.fstat(descriptor)
        _require_same_entry(before, opened, label="未完成标记")
        _require_no_extended_attributes(descriptor)
        if not 0 < opened.st_size <= policy.max_bytes:
            raise RuntimeFdTreeError("运行时对象未完成标记不受信任")
        content = _read_bounded(descriptor, policy.max_bytes)
        after = os.fstat(descriptor)
        _require_same_entry(opened, after, label="未完成标记")
        _require_same_entry(
            after,
            _stat_at(root_fd, policy.name),
            label="未完成标记",
        )
    finally:
        os.close(descriptor)
    if content not in policy.trusted_contents:
        raise RuntimeFdTreeError("运行时对象未完成标记阶段无效")
    return after.st_dev, after.st_ino, content


__all__ = [
    "CanonicalizeMode",
    "ConvergeCompletedAccessRepair",
    "PreflightCompletedAccessRepair",
    "PreflightGroup",
    "PublishGroup",
    "RuntimeFdGroupPolicy",
    "RuntimeFdModePolicy",
    "RuntimeFdTreeAccessRepairSnapshot",
    "RuntimeFdTreeError",
    "RuntimeFdTreeReport",
    "RuntimeFdTreeSnapshot",
    "RuntimeRootMarkerPolicy",
    "VerifyGroup",
    "VerifyOnly",
    "walk_runtime_tree",
    "walk_runtime_tree_from_cwd",
]
