"""attempt artifact 的固定路径、严格加载与幂等目录清理。"""
from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .attempt_completion import (
    AttemptCompletionReceipt,
    read_attempt_completion_receipt,
    validate_attempt_completion,
)
from .attempts import (
    AttemptJournalEntry,
    AttemptResult,
    AttemptSpec,
    ValidatedAttemptResult,
    read_attempt_result,
    read_attempt_spec,
    validate_attempt_result,
    write_attempt_result_atomic,
    write_attempt_spec_atomic,
)
from .file_durability import (
    durable_replace,
    durable_unlink,
    durable_write_once,
    fsync_directory,
)

if TYPE_CHECKING:
    from .queue_ports import ClaimedJob

_ATTEMPT_ID_MAX_BYTES = 512
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_FIXED_NAMES = frozenset({"spec.json", "result.json", "completion.json", "bootstrap.log"})
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_EXISTS_ERRORS = {80, 183}
_OWNED_FILE_TOMBSTONE_RE = re.compile(
    r"\.(?:spec\.json|result\.json|completion\.json|bootstrap\.log)\.[0-9a-f]{32}\.deleted\Z",
)


def _attempt_digest(attempt_id: object) -> str:
    if type(attempt_id) is not str or attempt_id != attempt_id.strip() or not attempt_id:
        raise ValueError("attempt_id 必须是非空规范字符串")
    try:
        encoded = attempt_id.encode("utf-8")
    except UnicodeError:
        raise ValueError("attempt_id 不是有效 UTF-8") from None
    if len(encoded) > _ATTEMPT_ID_MAX_BYTES or any(byte < 32 for byte in encoded):
        raise ValueError("attempt_id 超过上限或包含控制字符")
    return hashlib.sha256(encoded).hexdigest()


def _same_path(left: Path, right: Path) -> bool:
    return os.fspath(left) == os.fspath(right)


def _reject_windows_device_namespace(path: Path) -> None:
    if os.name != "nt":
        return
    normalized = os.fspath(path).replace("/", "\\")
    if normalized.startswith(("\\\\.\\", "\\\\?\\")):
        raise ValueError("artifact 根不能使用 Windows 设备命名空间")


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _require_directory(path: Path, *, missing_ok: bool = False) -> bool:
    info = _lstat(path)
    if info is None:
        if missing_ok:
            return False
        raise FileNotFoundError(path)
    if _is_reparse(info) or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("artifact 路径必须是非链接普通目录")
    return True


def _require_regular(path: Path, *, missing_ok: bool = False) -> bool:
    info = _lstat(path)
    if info is None:
        if missing_ok:
            return False
        raise FileNotFoundError(path)
    if _is_reparse(info) or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("artifact 文件必须是普通文件，不能是链接或 reparse point")
    return True


def _require_safe_existing_ancestor(path: Path) -> None:
    current = path
    found = False
    while True:
        if _lstat(current) is not None:
            _require_directory(current)
            found = True
        parent = current.parent
        if parent == current:
            if not found:
                raise ValueError("artifact 根没有可验证的既有父目录")
            return
        current = parent


def _current_windows_sid() -> str:
    """复用耐久叶子已经审计过的当前令牌身份解析。"""
    from . import file_durability

    return file_durability._windows_current_user_sid()  # noqa: SLF001


def _create_windows_private_directory(path: Path) -> None:
    """用 protected owner-only DACL 原子创建 attempt 目录。"""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    convert.restype = wintypes.BOOL
    create_directory = kernel32.CreateDirectoryW
    create_directory.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    create_directory.restype = wintypes.BOOL
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.DWORD),
            ("security_descriptor", ctypes.c_void_p),
            ("inherit_handle", wintypes.BOOL),
        ]

    descriptor = ctypes.c_void_p()
    sddl = f"D:P(A;;FA;;;{_current_windows_sid()})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes),
            descriptor,
            False,
        )
        created = create_directory(str(path), ctypes.byref(attributes))
        error = ctypes.get_last_error() if not created else 0
    finally:
        local_free(descriptor)
    if created:
        return
    if error in _WINDOWS_EXISTS_ERRORS:
        raise FileExistsError(errno.EEXIST, "attempt 目录已存在", str(path))
    raise ctypes.WinError(error)


@dataclass(frozen=True, slots=True)
class AttemptArtifactPaths:
    """一次 attempt 唯一允许使用的五个固定路径。"""

    root: Path
    spec: Path
    result: Path
    receipt: Path
    bootstrap_log: Path

    def __post_init__(self) -> None:
        for value in (self.root, self.spec, self.result, self.receipt, self.bootstrap_log):
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError("artifact 路径必须是绝对 Path")


class AttemptArtifactStore(Protocol):
    def expected(self, attempt_id: str) -> AttemptArtifactPaths: ...

    def initialize(self, spec: AttemptSpec) -> AttemptArtifactPaths: ...

    def verify_journal(self, entry: AttemptJournalEntry) -> AttemptArtifactPaths: ...

    def read_spec(self, paths: AttemptArtifactPaths) -> AttemptSpec: ...

    def read_result(self, paths: AttemptArtifactPaths) -> AttemptResult | None: ...

    def read_receipt(self, paths: AttemptArtifactPaths) -> AttemptCompletionReceipt | None: ...

    def load_validated(
        self,
        paths: AttemptArtifactPaths,
        claim: ClaimedJob,
    ) -> ValidatedAttemptResult: ...

    def write_result_once(self, paths: AttemptArtifactPaths, result: AttemptResult) -> None: ...

    def cleanup(self, paths: AttemptArtifactPaths) -> None: ...


def _identity(spec: AttemptSpec) -> tuple[object, ...]:
    return (
        spec.schema_version,
        spec.attempt_id,
        spec.fence,
        spec.project_id,
        spec.kind,
        spec.target_commit,
        spec.runtime_revision,
    )


def _result_identity(result: AttemptResult) -> tuple[object, ...]:
    return (
        result.schema_version,
        result.attempt_id,
        result.fence,
        result.project_id,
        result.kind,
        result.target_commit,
        result.runtime_revision,
    )


class FilesystemAttemptArtifactStore:
    """只管理给定受管根下按 attempt 摘要派生的目录。"""

    def __init__(self, root: Path) -> None:
        base = Path(root)
        _reject_windows_device_namespace(base)
        if not base.is_absolute():
            raise ValueError("artifact 根目录必须是绝对路径")
        canonical = Path(os.path.abspath(os.fspath(base)))
        if not _same_path(base, canonical):
            raise ValueError("artifact 根目录必须是规范绝对路径")
        _require_safe_existing_ancestor(base)
        self._root = base

    def expected(self, attempt_id: str) -> AttemptArtifactPaths:
        directory = self._root / _attempt_digest(attempt_id)
        return AttemptArtifactPaths(
            root=directory,
            spec=directory / "spec.json",
            result=directory / "result.json",
            receipt=directory / "completion.json",
            bootstrap_log=directory / "bootstrap.log",
        )

    def _validate_shape(self, paths: AttemptArtifactPaths) -> None:
        if type(paths) is not AttemptArtifactPaths:
            raise ValueError("paths 类型无效")
        if not _same_path(paths.root.parent, self._root):
            raise ValueError("artifact root 越出受管根")
        if _DIGEST_RE.fullmatch(paths.root.name) is None:
            raise ValueError("artifact root 不是小写 attempt 摘要")
        expected = {
            "spec": paths.root / "spec.json",
            "result": paths.root / "result.json",
            "receipt": paths.root / "completion.json",
            "bootstrap_log": paths.root / "bootstrap.log",
        }
        if any(not _same_path(getattr(paths, name), value) for name, value in expected.items()):
            raise ValueError("artifact 固定文件路径不匹配")

    def _prepare_base(self) -> None:
        _reject_windows_device_namespace(self._root)
        _require_safe_existing_ancestor(self._root)
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_directory(self._root)
        if os.name != "nt":
            os.chmod(self._root, 0o700)

    def _create_attempt_directory(self, path: Path) -> None:
        _reject_windows_device_namespace(path)
        if _lstat(path) is not None:
            _require_directory(path)
            raise FileExistsError(errno.EEXIST, "attempt 目录已存在", str(path))
        if os.name == "nt":
            _create_windows_private_directory(path)
        else:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
        _require_directory(path)
        fsync_directory(path.parent)

    def initialize(self, spec: AttemptSpec) -> AttemptArtifactPaths:
        if type(spec) is not AttemptSpec:
            raise ValueError("只接受 AttemptSpec")
        paths = self.expected(spec.attempt_id)
        self._prepare_base()
        tombstone = self._tombstone(paths)
        if _lstat(tombstone) is not None:
            _require_directory(tombstone)
            raise ValueError("attempt 仍有未完成的 cleanup tombstone")
        self._create_attempt_directory(paths.root)
        write_attempt_spec_atomic(paths.spec, spec)
        durable_write_once(paths.bootstrap_log, b"")
        return paths

    def _validate_existing(self, paths: AttemptArtifactPaths) -> None:
        self._validate_shape(paths)
        _require_directory(paths.root)
        names = {item.name for item in paths.root.iterdir()}
        extras = names - _FIXED_NAMES
        if extras:
            raise ValueError(f"artifact 目录包含额外条目：{sorted(extras)!r}")
        for name in names:
            _require_regular(paths.root / name)

    def read_spec(self, paths: AttemptArtifactPaths) -> AttemptSpec:
        self._validate_existing(paths)
        _require_regular(paths.spec)
        spec = read_attempt_spec(paths.spec)
        if self.expected(spec.attempt_id) != paths:
            raise ValueError("spec attempt_id 与 artifact 摘要路径不匹配")
        return spec

    def _read_result_file(self, paths: AttemptArtifactPaths) -> AttemptResult | None:
        if not _require_regular(paths.result, missing_ok=True):
            return None
        return read_attempt_result(paths.result)

    def _read_receipt_file(
        self,
        paths: AttemptArtifactPaths,
    ) -> AttemptCompletionReceipt | None:
        if not _require_regular(paths.receipt, missing_ok=True):
            return None
        return read_attempt_completion_receipt(paths.receipt)

    def read_result(self, paths: AttemptArtifactPaths) -> AttemptResult | None:
        spec = self.read_spec(paths)
        result = self._read_result_file(paths)
        if result is not None and _result_identity(result) != _identity(spec):
            raise ValueError("spec 与 result 身份不匹配")
        return result

    def read_receipt(self, paths: AttemptArtifactPaths) -> AttemptCompletionReceipt | None:
        spec = self.read_spec(paths)
        receipt = self._read_receipt_file(paths)
        if receipt is None:
            return None
        result = self._read_result_file(paths)
        if result is None:
            raise ValueError("存在 completion receipt 但缺少 result")
        validate_attempt_completion(spec, result, receipt)
        return receipt

    def load_validated(
        self,
        paths: AttemptArtifactPaths,
        claim: ClaimedJob,
    ) -> ValidatedAttemptResult:
        spec = self.read_spec(paths)
        result = self._read_result_file(paths)
        receipt = self._read_receipt_file(paths)
        if result is None or receipt is None:
            raise ValueError("attempt 尚未形成完整 result/receipt")
        process_rc = validate_attempt_completion(spec, result, receipt)
        return validate_attempt_result(
            spec,
            result,
            claim=claim,
            process_rc=process_rc,
            validated_at=receipt.observed_at,
        )

    def write_result_once(self, paths: AttemptArtifactPaths, result: AttemptResult) -> None:
        if type(result) is not AttemptResult:
            raise ValueError("只接受 AttemptResult")
        spec = self.read_spec(paths)
        if _result_identity(result) != _identity(spec):
            raise ValueError("父侧 result 与 spec 身份不匹配")
        write_attempt_result_atomic(paths.result, result)

    def verify_journal(self, entry: AttemptJournalEntry) -> AttemptArtifactPaths:
        if type(entry) is not AttemptJournalEntry:
            raise ValueError("只接受 AttemptJournalEntry")
        paths = self.expected(entry.attempt_id)
        if entry.spec_path != str(paths.spec) or entry.result_path != str(paths.result):
            raise ValueError("journal artifact 路径与固定派生路径不匹配")
        if not _require_directory(paths.root, missing_ok=True):
            return paths
        self._validate_existing(paths)
        if _require_regular(paths.spec, missing_ok=True):
            spec = self.read_spec(paths)
            expected = (entry.attempt_id, entry.fence, entry.project_id, entry.kind)
            actual = (spec.attempt_id, spec.fence, spec.project_id, spec.kind)
            if actual != expected:
                raise ValueError("journal 与 spec 身份不匹配")
        if _require_regular(paths.result, missing_ok=True):
            self.read_result(paths)
        if _require_regular(paths.receipt, missing_ok=True):
            self.read_receipt(paths)
        return paths

    def _tombstone(self, paths: AttemptArtifactPaths) -> Path:
        return paths.root.with_name(f"{paths.root.name}.cleanup")

    def _cleanup_file_tombstones(self, directory: Path) -> None:
        for item in tuple(directory.iterdir()):
            if _OWNED_FILE_TOMBSTONE_RE.fullmatch(item.name) is None:
                raise ValueError(f"artifact tombstone 包含额外条目：{item.name!r}")
            _require_regular(item)
            item.unlink()

    def _clean_directory(self, directory: Path) -> None:
        if not _require_directory(directory, missing_ok=True):
            return
        names = {item.name for item in directory.iterdir()}
        extras = {
            name
            for name in names - _FIXED_NAMES
            if _OWNED_FILE_TOMBSTONE_RE.fullmatch(name) is None
        }
        if extras:
            raise ValueError(f"artifact tombstone 包含额外条目：{sorted(extras)!r}")
        for name in sorted(names & _FIXED_NAMES):
            path = directory / name
            _require_regular(path)
            durable_unlink(path)
        self._cleanup_file_tombstones(directory)
        os.rmdir(directory)
        fsync_directory(directory.parent)

    def cleanup(self, paths: AttemptArtifactPaths) -> None:
        self._validate_shape(paths)
        tombstone = self._tombstone(paths)
        root_exists = _require_directory(paths.root, missing_ok=True)
        tombstone_exists = _require_directory(tombstone, missing_ok=True)
        if root_exists and tombstone_exists:
            raise ValueError("artifact root 与 cleanup tombstone 同时存在，状态分叉")
        if tombstone_exists:
            self._clean_directory(tombstone)
            return
        if not root_exists:
            return
        self._validate_existing(paths)
        durable_replace(paths.root, tombstone)
        self._clean_directory(tombstone)


__all__ = [
    "AttemptArtifactPaths",
    "AttemptArtifactStore",
    "FilesystemAttemptArtifactStore",
]
