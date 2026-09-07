"""版本化运行时的受管路径、POSIX 锁与半成品隔离。"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from codev_platform._runtime_flock import (
    RuntimeFlockTimeoutError,
    RuntimeFlockUnavailableError,
    acquire_runtime_flock,
)
from codev_platform._runtime_storage_isolation import (
    isolate_corrupt_completed_locked_at as isolate_corrupt_completed_locked_at,
    isolate_incomplete_locked_at as isolate_incomplete_locked_at,
)
from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_attempt_id,
    require_sha256,
)
from codev_platform.runtime_deployment_lock_capability import (
    BoundDeploymentLock,
    _issue_bound_deployment_lock,
    _revoke_bound_deployment_lock,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFileIdentityError,
    fsync_managed_directory,
    open_managed_regular_descriptor_at,
    verify_managed_regular_descriptor_at,
)
from codev_platform.runtime_object_lock_capability import (
    BoundRuntimeObjectLock,
    _issue_bound_runtime_object_lock,
    _revoke_bound_runtime_object_lock,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBinding,
    RuntimeRootBindingError,
)

_KINDS = {"base": "bases", "release": "releases"}
_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_DIRECTORY_MODE = 0o755
_LOCK_MODE = 0o600
_LOCK_WAIT_TIMEOUT_SEC = 30.0
_LOCK_RETRY_INTERVAL_SEC = 0.05


class RuntimeStorageError(RuntimeError):
    """运行时受管存储操作失败。"""


class RuntimeStoragePathError(RuntimeStorageError):
    """受管路径不满足安全边界。"""


class RuntimeStorageLockError(RuntimeStorageError):
    """运行时锁不可安全取得或释放。"""


def _require_posix() -> None:
    if os.name != "posix":
        raise RuntimeStorageLockError("运行时变更要求 POSIX flock 与目录 fsync")


def _identity(kind: object, object_id: object) -> tuple[str, str]:
    if type(kind) is not str or kind not in _KINDS:
        raise RuntimeStoragePathError("kind 只接受 base 或 release")
    valid = type(object_id) is str and _ID_PATTERN.fullmatch(object_id) is not None
    if not valid or set(object_id) == {"0"}:
        raise RuntimeStoragePathError("对象 ID 必须是完整非零 64 位小写 SHA-256")
    return kind, object_id


def _generation_id(value: object) -> str:
    valid = type(value) is str and _ID_PATTERN.fullmatch(value) is not None
    if not valid or set(value) == {"0"}:
        raise RuntimeStoragePathError("generation_id 必须是完整非零 64 位小写 SHA-256")
    return value


def _attempt_id(value: object) -> str:
    try:
        return require_attempt_id(value)
    except RuntimeContractSupportError as error:
        raise RuntimeStoragePathError(str(error)) from None


def _record_sha256(value: object, *, field: str) -> str:
    try:
        return require_sha256(value, field=field)
    except RuntimeContractSupportError as error:
        raise RuntimeStoragePathError(str(error)) from None


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise RuntimeStoragePathError(f"受管路径不可检查：{path.name}") from error


def _validate_directory(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeStoragePathError(f"受管路径不能包含符号链接：{path.name}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeStoragePathError(f"受管路径必须是目录：{path.name}")


def _validate_directory_chain(path: Path) -> None:
    for component in reversed((path, *path.parents)):
        _validate_directory(component)


def _normalize_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise RuntimeStoragePathError("受管根必须是绝对路径")
    normalized = Path(os.path.abspath(candidate))
    if candidate != normalized or normalized == Path(normalized.anchor):
        raise RuntimeStoragePathError("受管根不能包含路径逃逸或指向文件系统根")
    _validate_directory_chain(normalized)
    return normalized


def normalize_runtime_root(root: Path) -> Path:
    """返回通过受管目录链与符号链接边界校验的运行时根。"""
    return _normalize_root(root)


def _ensure_directory(path: Path) -> Path:
    for component in reversed((path, *path.parents)):
        metadata = _lstat(component)
        if metadata is None:
            try:
                os.mkdir(component, _DIRECTORY_MODE)
            except FileExistsError:
                pass
            except OSError as error:
                raise RuntimeStoragePathError(f"无法创建受管目录：{component.name}") from error
            _validate_directory(component)
            fsync_directory(component.parent)
            continue
        _validate_directory(component)
    return path


def _inside(root: Path, path: Path) -> Path:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise RuntimeStoragePathError("受管路径逃逸根目录") from error
    return path


def _validate_secure_directory(path: Path) -> None:
    metadata = _lstat(path)
    if (
        metadata is None
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RuntimeStoragePathError(f"受管目录所有权或权限不安全：{path.name}")


def fsync_directory(path: Path) -> None:
    """持久化目录项；拒绝跟随符号链接。"""
    _require_posix()
    directory = Path(path)
    _validate_directory_chain(directory)
    try:
        fsync_managed_directory(directory)
    except ManagedFileError as error:
        raise RuntimeStoragePathError(f"受管目录不可安全打开：{directory.name}") from error


def _flock(descriptor: int, *, shared: bool) -> None:
    try:
        acquire_runtime_flock(
            descriptor,
            shared=shared,
            timeout_sec=_LOCK_WAIT_TIMEOUT_SEC,
            retry_interval_sec=_LOCK_RETRY_INTERVAL_SEC,
        )
    except RuntimeFlockTimeoutError as error:
        raise RuntimeStorageLockError("等待运行时锁超过时间预算") from error
    except RuntimeFlockUnavailableError as error:
        raise RuntimeStorageLockError("POSIX flock 不可用") from error


@contextmanager
def _file_lock_at(
    path: Path,
    *,
    root: BoundRuntimeRoot,
    shared: bool,
) -> Iterator[int]:
    _require_posix()
    descriptor: int | None = None
    try:
        descriptor, _created = open_managed_regular_descriptor_at(
            path,
            root=root,
            mode=_LOCK_MODE,
            read_only=shared,
            create_missing=True,
        )
        _flock(descriptor, shared=shared)
        try:
            verify_managed_regular_descriptor_at(
                path,
                root=root,
                descriptor=descriptor,
                mode=_LOCK_MODE,
            )
            root.verify_visible()
            yield descriptor
        finally:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError as error:
                raise RuntimeStorageLockError("POSIX flock 无法安全释放") from error
            root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeStorageLockError("运行时根目录身份或可见路径已漂移") from error
    except ManagedFileIdentityError as error:
        raise RuntimeStorageLockError("锁文件所有权、权限或 inode 不安全") from error
    except ManagedFileError as error:
        raise RuntimeStoragePathError(str(error)) from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                raise RuntimeStorageLockError("锁文件 descriptor 无法关闭") from error


@contextmanager
def _bind_path_root(root: Path) -> Iterator[BoundRuntimeRoot]:
    managed_root = _normalize_root(root)
    _require_posix()
    try:
        binding = RuntimeRootBinding(managed_root, os.geteuid())
        with binding.bind() as bound_root:
            yield bound_root
    except RuntimeRootBindingError as error:
        raise RuntimeStoragePathError("运行时根目录不可安全绑定") from error


@contextmanager
def id_lock(
    root: Path,
    kind: str,
    object_id: str,
    *,
    shared: bool,
) -> Iterator[BoundRuntimeRoot]:
    """按 base/release 完整 ID 获取稳定的共享或排他 flock。"""
    resolved_kind, resolved_id = _identity(kind, object_id)
    if type(shared) is not bool:
        raise TypeError("shared 必须是布尔值")
    with _bind_path_root(root) as bound_root:
        with id_lock_at(bound_root, resolved_kind, resolved_id, shared=shared):
            yield bound_root


@contextmanager
def object_lock(
    root: Path,
    kind: str,
    object_id: str,
    *,
    shared: bool,
) -> Iterator[BoundRuntimeObjectLock]:
    """取得可验证、可吊销且绑定目标身份的对象 flock capability。"""
    resolved_kind, resolved_id = _identity(kind, object_id)
    if type(shared) is not bool:
        raise TypeError("shared 必须是布尔值")
    with _bind_path_root(root) as bound_root:
        with id_lock_at(bound_root, resolved_kind, resolved_id, shared=shared) as capability:
            yield capability


@contextmanager
def activation_lock(root: Path, *, shared: bool = False) -> Iterator[None]:
    """获取全局激活锁；读绑定可共享，版本切换保持默认排他。"""
    if type(shared) is not bool:
        raise TypeError("shared 必须是布尔值")
    with _bind_path_root(root) as bound_root:
        with activation_lock_at(bound_root, shared=shared):
            yield


@contextmanager
def deployment_lock(root: Path) -> Iterator[None]:
    """串行化整个部署状态机；与短时激活锁分离，避免扩大热路径锁范围。"""
    with _bind_path_root(root) as bound_root:
        with deployment_lock_at(bound_root):
            yield


@contextmanager
def id_lock_at(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    shared: bool,
) -> Iterator[BoundRuntimeObjectLock]:
    """在同一根租约内获取按对象划分的共享或排他锁并签发 capability。"""
    resolved_kind, resolved_id = _identity(kind, object_id)
    if type(shared) is not bool:
        raise TypeError("shared 必须是布尔值")
    _require_bound_root(root)
    with _file_lock_at(
        root.path / "locks" / resolved_kind / f"{resolved_id}.lock",
        root=root,
        shared=shared,
    ) as descriptor:
        capability = _issue_bound_runtime_object_lock(
            root,
            resolved_kind,
            resolved_id,
            descriptor,
            shared=shared,
        )
        try:
            yield capability
        finally:
            _revoke_bound_runtime_object_lock(capability)


@contextmanager
def activation_lock_at(root: BoundRuntimeRoot, *, shared: bool = False) -> Iterator[None]:
    """在同一根租约内获取全局激活锁。"""
    if type(shared) is not bool:
        raise TypeError("shared 必须是布尔值")
    _require_bound_root(root)
    with _file_lock_at(root.path / "locks" / "activation.lock", root=root, shared=shared):
        yield


@contextmanager
def deployment_lock_at(root: BoundRuntimeRoot) -> Iterator[BoundDeploymentLock]:
    """在同一根租约内串行化状态机，并签发活跃锁能力。"""
    _require_bound_root(root)
    with _file_lock_at(
        root.path / "locks" / "deployment.lock",
        root=root,
        shared=False,
    ) as descriptor:
        capability = _issue_bound_deployment_lock(root, descriptor)
        try:
            yield capability
        finally:
            _revoke_bound_deployment_lock(capability)


@contextmanager
def isolation_journal_lock_at(root: BoundRuntimeRoot) -> Iterator[None]:
    """在已持对象锁的同一根租约内串行化隔离审计 journal。"""
    _require_bound_root(root)
    with _file_lock_at(
        root.path / "journal" / "runtime-storage.lock",
        root=root,
        shared=False,
    ):
        yield


def _require_bound_root(root: BoundRuntimeRoot) -> None:
    if type(root) is not BoundRuntimeRoot:
        raise RuntimeStoragePathError("运行时根租约类型无效")
    try:
        root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeStorageLockError("运行时根目录身份或可见路径已漂移") from error


def generation_record_path(root: Path, generation_id: str) -> Path:
    """返回不可变代际记录的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "generations" / _generation_id(generation_id) / "generation.json",
    )


def attempt_reservation_path(root: Path, attempt_id: str) -> Path:
    """返回部署尝试预留记录的固定受管路径。"""
    return _attempt_file_path(root, attempt_id, "reservation.json")


def attempt_record_path(root: Path, attempt_id: str) -> Path:
    """返回冻结部署尝试的固定受管路径。"""
    return _attempt_file_path(root, attempt_id, "attempt.json")


def attempt_journal_path(root: Path, attempt_id: str) -> Path:
    """返回部署事务 journal 的固定受管路径。"""
    return _attempt_file_path(root, attempt_id, "journal.json")


def attempt_recovery_envelope_path(root: Path, attempt_id: str) -> Path:
    """返回 attempt 专属恢复 envelope 的固定受管路径。"""
    return _attempt_file_path(root, attempt_id, "recovery-envelope.json")


def attempt_terminal_evidence_path(root: Path, attempt_id: str) -> Path:
    """返回 attempt 专属终态证据的固定受管路径。"""
    return _attempt_file_path(root, attempt_id, "terminal-evidence.json")


def rollback_bundle_path(root: Path, attempt_id: str) -> Path:
    """返回回滚包记录的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "rollback-bundles" / _attempt_id(attempt_id) / "bundle.json",
    )


def rollback_bundle_pending_path(root: Path, attempt_id: str) -> Path:
    """返回回滚 bundle 两阶段封口前的固定暂存路径。"""
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "rollback-bundles" / _attempt_id(attempt_id) / "bundle.pending.json",
    )


def acceptance_record_path(root: Path, attempt_id: str) -> Path:
    """返回 attempt 独立验收记录的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "acceptances" / f"{_attempt_id(attempt_id)}.json",
    )


def generation_state_path(root: Path) -> Path:
    """返回单一代际状态文件的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(managed_root, managed_root / "generation-state.json")


def active_recovery_envelope_path(root: Path) -> Path:
    """返回唯一活动恢复 envelope 的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(managed_root, managed_root / "active-recovery-envelope.json")


def control_lease_record_path(root: Path) -> Path:
    """返回唯一公开 control lease 记录的固定受管路径。"""
    managed_root = _normalize_root(root)
    return _inside(managed_root, managed_root / "control-lease.json")


def control_lease_pending_transition_path(root: Path) -> Path:
    """返回唯一 pending control lease transition 的固定根叶子。"""
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "control-lease-pending-transition.json",
    )


def control_lease_history_path(
    root: Path,
    attempt_id: str,
    record_sha256: str,
) -> Path:
    """返回不可变 control lease 历史记录的固定受管路径。"""
    return _attempt_digest_record_path(
        root,
        "control-leases",
        attempt_id,
        record_sha256,
        field="control_lease_record_sha256",
    )


def serving_fence_record_path(
    root: Path,
    attempt_id: str,
    record_sha256: str,
) -> Path:
    """返回不可变 serving fence 记录的固定受管路径。"""
    return _attempt_digest_record_path(
        root,
        "serving-fences",
        attempt_id,
        record_sha256,
        field="serving_fence_record_sha256",
    )


def serving_permit_record_path(
    root: Path,
    attempt_id: str,
    record_sha256: str,
) -> Path:
    """返回不可变 serving permit 记录的固定受管路径。"""
    return _attempt_digest_record_path(
        root,
        "serving-permits",
        attempt_id,
        record_sha256,
        field="serving_permit_record_sha256",
    )


def serving_permit_stage_path(
    root: Path,
    attempt_id: str,
    staged_state_sha256: str,
) -> Path:
    """返回按维护态 A 摘要唯一寻址的 staged permit 锚点。"""
    return _attempt_digest_record_path(
        root,
        "serving-permit-stages",
        attempt_id,
        staged_state_sha256,
        field="staged_generation_state_sha256",
    )


def _attempt_file_path(root: Path, attempt_id: str, leaf: str) -> Path:
    managed_root = _normalize_root(root)
    return _inside(
        managed_root,
        managed_root / "attempts" / _attempt_id(attempt_id) / leaf,
    )


def _attempt_digest_record_path(
    root: Path,
    collection: str,
    attempt_id: str,
    record_sha256: str,
    *,
    field: str,
) -> Path:
    managed_root = _normalize_root(root)
    resolved_attempt_id = _attempt_id(attempt_id)
    resolved_sha256 = _record_sha256(record_sha256, field=field)
    return _inside(
        managed_root,
        managed_root / collection / resolved_attempt_id / f"{resolved_sha256}.json",
    )


def initialize_runtime_root(root: Path) -> Path:
    """由 root 创建一次正式内容寻址目录骨架，并拒绝接管不安全旧路径。"""
    _require_posix()
    if os.geteuid() != 0:
        raise RuntimeStoragePathError("正式运行时根只能由 root 初始化")
    managed_root = _normalize_root(root)
    directories = (
        managed_root,
        managed_root / "bases",
        managed_root / "releases",
        managed_root / "candidates",
        managed_root / "deployments",
        managed_root / "generations",
        managed_root / "attempts",
        managed_root / "rollback-bundles",
        managed_root / "acceptances",
        managed_root / "control-leases",
        managed_root / "serving-fences",
        managed_root / "serving-permits",
        managed_root / "serving-permit-stages",
        managed_root / "locks",
    )
    for directory in directories:
        _ensure_directory(directory)
    for directory in directories:
        _validate_secure_directory(directory)
    return managed_root
