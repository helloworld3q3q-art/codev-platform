"""可证明生成字节码的通用 fd-relative 清理机械层。"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath


_PLAN_SEAL = object()
_SPECIAL_PERMISSION_BITS = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX


class RuntimeGeneratedBytecodeError(RuntimeError):
    """生成字节码无法形成可安全删除的完整证明。"""


@dataclass(frozen=True, slots=True)
class GeneratedBytecodeCleanupResult:
    """通用删除结果；验证证明的具体类型由上层完整性适配器决定。"""

    deleted_files: int
    deleted_cache_directories: int
    verification_proof: object


@dataclass(frozen=True, slots=True)
class GeneratedBytecodeInventory:
    """上层完整性真值向机械清理层提供的只读快照。"""

    actual: dict[str, Path]
    directories: set[str]
    known_files: frozenset[str]
    source_files: frozenset[str]
    verify_virtual: Callable[[dict[str, Path], set[str]], object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Candidate:
    relative: PurePosixPath
    identity: tuple[int, ...]


@dataclass(frozen=True, slots=True, init=False)
class GeneratedBytecodePlan:
    """只能由全量完整性预检生成的不可伪造清理计划。"""

    _root: Path = field(repr=False)
    _root_identity: tuple[int, ...] = field(repr=False)
    _candidates: tuple[_Candidate, ...] = field(repr=False)
    _cache_directories: tuple[PurePosixPath, ...] = field(repr=False)
    _verification_proof: object = field(repr=False)
    _kind: str = field(repr=False)
    _snapshot_factory: Callable[[Path], GeneratedBytecodeInventory] = field(repr=False)
    _strict_verifier: Callable[[Path], object] = field(repr=False)
    _seal: object = field(repr=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("生成字节码清理计划只能由预检生成")

    @property
    def candidate_count(self) -> int:
        return len(self._candidates)

    @property
    def cache_directory_count(self) -> int:
        return len(self._cache_directories)

    @property
    def verification_proof(self) -> object:
        return self._verification_proof

    @property
    def inventory_proof(self) -> object:
        """兼容 base 适配器的既有证明名称。"""
        return self._verification_proof


def plan_generated_bytecode_cleanup(
    root: Path,
    *,
    kind: str,
    snapshot_factory: Callable[[Path], GeneratedBytecodeInventory],
    strict_verifier: Callable[[Path], object],
) -> GeneratedBytecodePlan:
    """先虚拟删除并复用上层严格 verifier，成功后才形成删除计划。"""
    try:
        selected_root = Path(root)
        root_identity = _directory_identity(selected_root)
        inventory = snapshot_factory(selected_root)
        _require_inventory(inventory)
        actual = inventory.actual
        missing = set(inventory.known_files) - set(actual)
        if missing:
            raise RuntimeGeneratedBytecodeError("生成字节码预检发现已知文件缺失")
        candidates = _validated_candidates(
            selected_root,
            actual,
            inventory.known_files,
            inventory.source_files,
        )
        candidate_relatives = {candidate.relative.as_posix() for candidate in candidates}
        if candidate_relatives != set(actual) - set(inventory.known_files):
            raise RuntimeGeneratedBytecodeError("存在非生成字节码未知文件")
        remaining = {
            relative: path
            for relative, path in actual.items()
            if relative not in candidate_relatives
        }
        cache_directories = _empty_cache_directories(candidates, remaining)
        verification_proof = inventory.verify_virtual(
            remaining,
            _virtual_directories(inventory.directories, cache_directories),
        )
        return _new_plan(
            root=selected_root,
            root_identity=root_identity,
            candidates=candidates,
            cache_directories=cache_directories,
            verification_proof=verification_proof,
            kind=kind,
            snapshot_factory=snapshot_factory,
            strict_verifier=strict_verifier,
        )
    except RuntimeGeneratedBytecodeError:
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeGeneratedBytecodeError("生成字节码预检无法安全完成") from None


def apply_generated_bytecode_cleanup(
    plan: GeneratedBytecodePlan,
    *,
    kind: str,
) -> GeneratedBytecodeCleanupResult:
    """以 fd-relative 删除已预检缓存，并立即调用上层严格 verifier。"""
    _require_linux_root()
    _require_plan_kind(plan, kind)
    try:
        current = plan_generated_bytecode_cleanup(
            plan._root,
            kind=plan._kind,
            snapshot_factory=plan._snapshot_factory,
            strict_verifier=plan._strict_verifier,
        )
        if _plan_identity(current) != _plan_identity(plan):
            raise RuntimeGeneratedBytecodeError("生成字节码预检快照发生漂移")
        with _open_root(plan._root, plan._root_identity) as root_fd:
            for candidate in plan._candidates:
                _unlink_candidate(root_fd, candidate)
            for directory in plan._cache_directories:
                _remove_empty_cache_directory(root_fd, directory)
        verification_proof = plan._strict_verifier(plan._root)
        if verification_proof != plan.verification_proof:
            raise RuntimeGeneratedBytecodeError("生成字节码清理后完整性证明漂移")
        return GeneratedBytecodeCleanupResult(
            deleted_files=plan.candidate_count,
            deleted_cache_directories=plan.cache_directory_count,
            verification_proof=verification_proof,
        )
    except RuntimeGeneratedBytecodeError:
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeGeneratedBytecodeError("生成字节码无法安全清理") from None


def _require_inventory(inventory: object) -> None:
    if (
        type(inventory) is not GeneratedBytecodeInventory
        or type(inventory.actual) is not dict
        or type(inventory.directories) is not set
        or type(inventory.known_files) is not frozenset
        or type(inventory.source_files) is not frozenset
        or not callable(inventory.verify_virtual)
        or not inventory.source_files.issubset(inventory.known_files)
    ):
        raise RuntimeGeneratedBytecodeError("生成字节码完整性快照无效")
    if any(
        type(relative) is not str or not isinstance(path, Path)
        for relative, path in inventory.actual.items()
    ):
        raise RuntimeGeneratedBytecodeError("生成字节码完整性文件快照无效")
    if any(type(value) is not str for value in inventory.directories | inventory.known_files):
        raise RuntimeGeneratedBytecodeError("生成字节码完整性路径快照无效")


def _validated_candidates(
    root: Path,
    actual: dict[str, Path],
    known_files: frozenset[str],
    source_files: frozenset[str],
) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    for relative in sorted(set(actual) - set(known_files)):
        path = actual[relative]
        selected = PurePosixPath(relative)
        _require_cache_shape(selected)
        _require_claimed_source(root, path, selected, actual, source_files)
        candidates.append(_Candidate(selected, _candidate_identity(path)))
    return tuple(candidates)


def _require_cache_shape(relative: PurePosixPath) -> None:
    if (
        relative.is_absolute()
        or len(relative.parts) < 2
        or relative.parent.name != "__pycache__"
        or relative.suffix != ".pyc"
    ):
        raise RuntimeGeneratedBytecodeError("未知文件不是受控生成字节码")


def _require_claimed_source(
    root: Path,
    path: Path,
    relative: PurePosixPath,
    actual: dict[str, Path],
    source_files: frozenset[str],
) -> None:
    try:
        source = Path(importlib.util.source_from_cache(os.fspath(path)))
        source_relative = source.relative_to(root).as_posix()
    except (ValueError, OSError):
        raise RuntimeGeneratedBytecodeError("生成字节码无法映射登记源文件") from None
    if source_relative not in actual or source_relative not in source_files:
        raise RuntimeGeneratedBytecodeError("生成字节码源文件未被登记")
    if relative.parent != PurePosixPath(source_relative).parent / "__pycache__":
        raise RuntimeGeneratedBytecodeError("生成字节码缓存目录不受信任")
    expected_cache = Path(importlib.util.cache_from_source(os.fspath(source)))
    if path != expected_cache:
        raise RuntimeGeneratedBytecodeError("生成字节码缓存标签不受支持")


def _candidate_identity(path: Path) -> tuple[int, ...]:
    descriptor = -1
    try:
        before = path.lstat()
        _require_candidate_metadata(before)
        descriptor = os.open(path, _file_open_flags())
        opened = os.fstat(descriptor)
        _require_same_identity(before, opened)
        header = os.read(descriptor, 16)
        after = os.fstat(descriptor)
        _require_same_identity(opened, after)
        if len(header) < 16 or header[:4] != importlib.util.MAGIC_NUMBER:
            raise RuntimeGeneratedBytecodeError("生成字节码 magic 不受支持")
        return _identity(after)
    except RuntimeGeneratedBytecodeError:
        raise
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码无法安全读取") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _virtual_directories(
    directories: set[str],
    cache_directories: tuple[PurePosixPath, ...],
) -> set[str]:
    """只移除已证明会被删除的空缓存目录，保留其他空目录门禁。"""
    removed = {directory.as_posix() for directory in cache_directories}
    return directories - removed


def _empty_cache_directories(
    candidates: tuple[_Candidate, ...],
    remaining: dict[str, Path],
) -> tuple[PurePosixPath, ...]:
    directories = {candidate.relative.parent for candidate in candidates}
    result = [
        directory
        for directory in directories
        if not any(PurePosixPath(relative).is_relative_to(directory) for relative in remaining)
    ]
    return tuple(sorted(result, key=lambda item: (-len(item.parts), item.as_posix())))


def _new_plan(
    *,
    root: Path,
    root_identity: tuple[int, ...],
    candidates: tuple[_Candidate, ...],
    cache_directories: tuple[PurePosixPath, ...],
    verification_proof: object,
    kind: str,
    snapshot_factory: Callable[[Path], GeneratedBytecodeInventory],
    strict_verifier: Callable[[Path], object],
) -> GeneratedBytecodePlan:
    if (
        not root.is_absolute()
        or type(root_identity) is not tuple
        or not root_identity
        or type(kind) is not str
        or not kind
        or not callable(snapshot_factory)
        or not callable(strict_verifier)
        or any(type(candidate) is not _Candidate for candidate in candidates)
        or any(type(directory) is not PurePosixPath for directory in cache_directories)
    ):
        raise RuntimeGeneratedBytecodeError("生成字节码清理计划无效")
    result = object.__new__(GeneratedBytecodePlan)
    object.__setattr__(result, "_root", root)
    object.__setattr__(result, "_root_identity", root_identity)
    object.__setattr__(result, "_candidates", candidates)
    object.__setattr__(result, "_cache_directories", cache_directories)
    object.__setattr__(result, "_verification_proof", verification_proof)
    object.__setattr__(result, "_kind", kind)
    object.__setattr__(result, "_snapshot_factory", snapshot_factory)
    object.__setattr__(result, "_strict_verifier", strict_verifier)
    object.__setattr__(result, "_seal", _PLAN_SEAL)
    return result


def _require_plan_kind(plan: GeneratedBytecodePlan, kind: str) -> None:
    if type(plan) is not GeneratedBytecodePlan or getattr(plan, "_seal", None) is not _PLAN_SEAL:
        raise RuntimeGeneratedBytecodeError("生成字节码清理计划不受信任")
    if type(kind) is not str or plan._kind != kind:
        raise RuntimeGeneratedBytecodeError("生成字节码清理计划类型不匹配")
    trusted = _new_plan(
        root=plan._root,
        root_identity=plan._root_identity,
        candidates=plan._candidates,
        cache_directories=plan._cache_directories,
        verification_proof=plan._verification_proof,
        kind=plan._kind,
        snapshot_factory=plan._snapshot_factory,
        strict_verifier=plan._strict_verifier,
    )
    if _plan_identity(trusted) != _plan_identity(plan):
        raise RuntimeGeneratedBytecodeError("生成字节码清理计划不受信任")


def _plan_identity(plan: GeneratedBytecodePlan) -> tuple[object, ...]:
    return (
        plan._root,
        plan._root_identity,
        plan._candidates,
        plan._cache_directories,
        plan._verification_proof,
        plan._kind,
    )


def _unlink_candidate(root_fd: int, candidate: _Candidate) -> None:
    with _open_parent(root_fd, candidate.relative.parent) as parent_fd:
        metadata = _stat_at(parent_fd, candidate.relative.name)
        _require_candidate_metadata(metadata)
        if _identity(metadata) != candidate.identity:
            raise RuntimeGeneratedBytecodeError("生成字节码删除前身份发生漂移")
        descriptor = _open_regular_at(parent_fd, candidate.relative.name)
        try:
            current = os.fstat(descriptor)
            _require_same_identity(metadata, current)
            if os.read(descriptor, 16)[:4] != importlib.util.MAGIC_NUMBER:
                raise RuntimeGeneratedBytecodeError("生成字节码删除前 magic 发生漂移")
            _require_same_identity(current, os.fstat(descriptor))
        finally:
            os.close(descriptor)
        os.unlink(candidate.relative.name, dir_fd=parent_fd)
        os.fsync(parent_fd)


def _remove_empty_cache_directory(root_fd: int, directory: PurePosixPath) -> None:
    with _open_parent(root_fd, directory.parent) as parent_fd:
        metadata = _stat_at(parent_fd, directory.name)
        _require_directory_metadata(metadata)
        descriptor = _open_directory_at(parent_fd, directory.name)
        try:
            with os.scandir(descriptor) as entries:
                if any(entries):
                    raise RuntimeGeneratedBytecodeError("生成字节码缓存目录在删除期间发生漂移")
        finally:
            os.close(descriptor)
        os.rmdir(directory.name, dir_fd=parent_fd)
        os.fsync(parent_fd)


@contextmanager
def _open_root(root: Path, expected_identity: tuple[int, ...]) -> Iterator[int]:
    descriptor = _open_directory(root)
    try:
        if _identity(os.fstat(descriptor)) != expected_identity:
            raise RuntimeGeneratedBytecodeError("生成字节码根目录身份发生漂移")
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _open_parent(root_fd: int, relative: PurePosixPath) -> Iterator[int]:
    descriptor = os.dup(root_fd)
    try:
        for part in relative.parts:
            metadata = _stat_at(descriptor, part)
            _require_directory_metadata(metadata)
            child = _open_directory_at(descriptor, part)
            try:
                _require_same_identity(metadata, os.fstat(child))
            except BaseException:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _open_directory(root: Path) -> int:
    try:
        descriptor = os.open(root, _directory_open_flags())
        _require_directory_metadata(os.fstat(descriptor))
        return descriptor
    except RuntimeGeneratedBytecodeError:
        raise
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码根目录不可安全打开") from None


def _directory_identity(path: Path) -> tuple[int, ...]:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码根目录不可安全读取") from None
    _require_directory_metadata(metadata)
    return _identity(metadata)


def _open_directory_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _directory_open_flags(), dir_fd=parent_fd)
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码目录不可安全打开") from None


def _open_regular_at(parent_fd: int, name: str) -> int:
    try:
        return os.open(name, _file_open_flags(), dir_fd=parent_fd)
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码文件不可安全打开") from None


def _stat_at(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise RuntimeGeneratedBytecodeError("生成字节码目录项不可安全读取") from None


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOATIME", 0)
    )


def _file_open_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOATIME", 0)
        | getattr(os, "O_BINARY", 0)
    )


def _require_candidate_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeGeneratedBytecodeError("生成字节码必须是普通文件")
    _require_root_controlled_metadata(metadata)
    if metadata.st_nlink != 1:
        raise RuntimeGeneratedBytecodeError("生成字节码不能是硬链接")


def _require_directory_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeGeneratedBytecodeError("生成字节码目录类型无效")
    _require_root_controlled_metadata(metadata)
    if stat.S_IMODE(metadata.st_mode) & 0o500 != 0o500:
        raise RuntimeGeneratedBytecodeError("生成字节码目录缺少 root 访问权限")


def _require_root_controlled_metadata(metadata: os.stat_result) -> None:
    if not _is_linux_posix():
        # 实际删除入口仅允许 Linux root；非目标平台只用于结构性单元测试。
        return
    permissions = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != 0:
        raise RuntimeGeneratedBytecodeError("生成字节码对象必须由 root 所有")
    if permissions & (_SPECIAL_PERMISSION_BITS | 0o022):
        raise RuntimeGeneratedBytecodeError("生成字节码对象权限不安全")


def _require_same_identity(before: os.stat_result, after: os.stat_result) -> None:
    if _identity(before) != _identity(after):
        raise RuntimeGeneratedBytecodeError("生成字节码对象身份发生漂移")


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    # Windows 打开文件可能刷新 ctime；实际删除仅支持 Linux root，Linux 仍保留 ctime。
    return identity + ((metadata.st_ctime_ns,) if _is_linux_posix() else ())


def _require_linux_root() -> None:
    if not _is_linux_posix() or os.geteuid() != 0:
        raise RuntimeGeneratedBytecodeError("生成字节码清理仅支持 Linux root")


def _is_linux_posix() -> bool:
    return os.name == "posix" and sys.platform.startswith("linux")


__all__ = [
    "GeneratedBytecodeCleanupResult",
    "GeneratedBytecodeInventory",
    "GeneratedBytecodePlan",
    "RuntimeGeneratedBytecodeError",
    "apply_generated_bytecode_cleanup",
    "plan_generated_bytecode_cleanup",
]
