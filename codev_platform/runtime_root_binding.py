"""运行时根目录的短生命周期 descriptor 租约。"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH


class RuntimeRootBindingError(ValueError):
    """运行时根的身份、可见性或租约生命周期不满足边界。"""


@dataclass(frozen=True, slots=True)
class _RuntimeRootIdentity:
    """仅用于同一运行时根的目录身份比较。"""

    device: int
    inode: int
    owner_uid: int
    mode: int


class BoundRuntimeRoot:
    """仅在 ``RuntimeRootBinding.bind()`` 作用域内有效的根目录租约。"""

    __slots__ = ("_binding", "_descriptor", "_identity", "_owner_uid", "_path")

    def __init__(
        self,
        binding: RuntimeRootBinding,
        descriptor: int,
        identity: _RuntimeRootIdentity,
    ) -> None:
        self._binding = binding
        self._descriptor = descriptor
        self._identity = identity
        self._owner_uid = binding.owner_uid
        self._path = binding.path

    @property
    def path(self) -> Path:
        """返回仅用于派生受管相对路径的规范绝对路径。"""
        return self._path

    @property
    def owner_uid(self) -> int:
        """返回此根租约要求的目录与文件属主。"""
        return self._owner_uid

    def verify_visible(self) -> None:
        """比较命名根与持有 descriptor 的冻结身份，绝不用于真实 I/O。"""
        self._ensure_open()
        try:
            descriptor = _open_absolute_directory(self._path)
            actual = _require_secure_root_identity(descriptor, self._owner_uid)
            if actual != self._identity:
                raise RuntimeRootBindingError("运行时根目录身份或可见路径已漂移")
        except RuntimeRootBindingError as error:
            self._binding._record_drift(error)
            raise
        finally:
            if "descriptor" in locals():
                _close_quietly(descriptor)

    def relative_path(self, path: Path) -> tuple[str, ...]:
        """将规范绝对受管路径转换为无逃逸的相对组件。"""
        self._ensure_open()
        return validate_runtime_relative_path(path, root=self._path)

    def _duplicate_root_fd(self) -> int:
        """仅供受管文件机械层取得 close-on-exec 的短暂副本。"""
        self._ensure_open()
        duplicate: int | None = None
        try:
            current = _require_secure_root_identity(self._descriptor, self._owner_uid)
            if current != self._identity:
                raise RuntimeRootBindingError("运行时根目录身份已漂移")
            duplicate = os.dup(self._descriptor)
            os.set_inheritable(duplicate, False)
            opened = duplicate
            duplicate = None
            return opened
        except RuntimeRootBindingError:
            self._binding._record_drift(RuntimeRootBindingError("运行时根目录身份已漂移"))
            raise
        except OSError as error:
            self._binding._record_drift(error)
            raise RuntimeRootBindingError("运行时根目录 descriptor 无法复制") from error
        finally:
            if duplicate is not None:
                _close_quietly(duplicate)

    def _ensure_open(self) -> None:
        self._binding._raise_if_poisoned()
        if self._descriptor is None:
            error = RuntimeRootBindingError("运行时根租约已关闭")
            self._binding._record_drift(error)
            raise error

    def _close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            _close_quietly(descriptor)


class RuntimeRootBinding:
    """冻结单一 policy 的根目录身份并签发短生命周期租约。"""

    def __init__(self, root: Path, owner_uid: int) -> None:
        self._path = validate_runtime_root_path(root)
        if type(owner_uid) is not int or owner_uid < 0:
            raise RuntimeRootBindingError("运行时根属主必须是非负整数")
        self._owner_uid = owner_uid
        self._identity: _RuntimeRootIdentity | None = None
        self._lock = threading.RLock()
        self._poison_reason: str | None = None

    @property
    def path(self) -> Path:
        """返回构造期确认的规范根路径。"""
        return self._path

    @property
    def owner_uid(self) -> int:
        """返回根目录冻结时要求的属主。"""
        return self._owner_uid

    @contextmanager
    def bind(self) -> Iterator[BoundRuntimeRoot]:
        """打开一次根目录并在进入、退出时复验可见路径身份。"""
        self._raise_if_poisoned()
        try:
            descriptor = _open_absolute_directory(self._path)
        except RuntimeRootBindingError as error:
            self._record_drift(error)
            raise
        bound_root: BoundRuntimeRoot | None = None
        try:
            try:
                identity = _require_secure_root_identity(descriptor, self._owner_uid)
            except RuntimeRootBindingError as error:
                self._record_drift(error)
                raise
            self._accept_identity(identity)
            bound_root = BoundRuntimeRoot(self, descriptor, identity)
            descriptor = None
            bound_root.verify_visible()
            yield bound_root
        finally:
            if bound_root is not None:
                try:
                    bound_root.verify_visible()
                finally:
                    bound_root._close()
            elif descriptor is not None:
                _close_quietly(descriptor)

    @contextmanager
    def bind_inherited_descriptor(self, descriptor: int) -> Iterator[BoundRuntimeRoot]:
        """以 worker 继承的根 descriptor 签发租约，不通过路径重新打开真实根。"""
        self._raise_if_poisoned()
        if type(descriptor) is not int or descriptor < 0:
            raise RuntimeRootBindingError("继承的运行时根 descriptor 无效")
        duplicate: int | None = None
        bound_root: BoundRuntimeRoot | None = None
        try:
            try:
                duplicate = os.dup(descriptor)
                os.set_inheritable(duplicate, False)
                identity = _require_secure_root_identity(duplicate, self._owner_uid)
            except RuntimeRootBindingError as error:
                self._record_drift(error)
                raise
            except OSError as error:
                binding_error = RuntimeRootBindingError(
                    "继承的运行时根 descriptor 无法复制",
                )
                self._record_drift(binding_error)
                raise binding_error from error
            self._accept_identity(identity)
            bound_root = BoundRuntimeRoot(self, duplicate, identity)
            duplicate = None
            bound_root.verify_visible()
            yield bound_root
        finally:
            if bound_root is not None:
                try:
                    bound_root.verify_visible()
                finally:
                    bound_root._close()
            elif duplicate is not None:
                _close_quietly(duplicate)

    def verify(self) -> None:
        """建立或复验当前命名根与同一冻结身份一致。"""
        with self.bind():
            return None

    def require_bound(self, root: BoundRuntimeRoot) -> None:
        """拒绝不同 policy、关闭或可见性漂移的根租约。"""
        self._raise_if_poisoned()
        if type(root) is not BoundRuntimeRoot or root._binding is not self:
            raise RuntimeRootBindingError("运行时根租约来自其他绑定")
        root.verify_visible()

    def _accept_identity(self, identity: _RuntimeRootIdentity) -> None:
        with self._lock:
            if self._poison_reason is not None:
                raise RuntimeRootBindingError(
                    f"运行时根绑定已永久失效：{self._poison_reason}",
                )
            if self._identity is None:
                self._identity = identity
            elif self._identity != identity:
                self._poison_reason = "运行时根目录身份已漂移"
                raise RuntimeRootBindingError("运行时根目录身份已漂移")

    def _record_drift(self, error: BaseException) -> None:
        with self._lock:
            if self._identity is not None and self._poison_reason is None:
                self._poison_reason = str(error) or "运行时根目录边界校验失败"

    def _raise_if_poisoned(self) -> None:
        with self._lock:
            if self._poison_reason is not None:
                raise RuntimeRootBindingError(
                    f"运行时根绑定已永久失效：{self._poison_reason}",
                )


def _canonical_absolute_path(value: Path, *, field: str) -> Path:
    if not isinstance(value, Path):
        raise RuntimeRootBindingError(f"{field}必须是 Path")
    try:
        text = str(value)
    except (TypeError, ValueError) as error:
        raise RuntimeRootBindingError(f"{field}无效") from error
    if "\x00" in text:
        raise RuntimeRootBindingError(f"{field}不能包含 NUL")
    if not value.is_absolute():
        raise RuntimeRootBindingError(f"{field}必须是绝对路径")
    normalized = Path(os.path.abspath(value))
    if value != normalized:
        raise RuntimeRootBindingError(f"{field}不能包含路径逃逸或非规范组件")
    return normalized


def validate_runtime_relative_path(path: Path, *, root: Path) -> tuple[str, ...]:
    """在不触碰文件系统前验证受管文件路径的词法边界。"""
    target = _canonical_absolute_path(path, field="受管文件路径")
    managed_root = validate_runtime_root_path(root)
    try:
        relative = target.relative_to(managed_root)
    except ValueError as error:
        raise RuntimeRootBindingError("受管文件路径逃逸运行时根目录") from error
    if relative == Path(".") or not relative.parts:
        raise RuntimeRootBindingError("受管文件路径必须包含叶子名称")
    parts = tuple(relative.parts)
    if any(part in {"", ".", ".."} or "\x00" in part for part in parts):
        raise RuntimeRootBindingError("受管文件路径包含非法组件")
    return parts


def validate_runtime_root_path(root: Path) -> Path:
    """在不触碰文件系统前验证运行时根的词法边界。"""
    normalized = _canonical_absolute_path(root, field="运行时根路径")
    if normalized == Path(normalized.anchor):
        raise RuntimeRootBindingError("运行时根路径不能是文件系统根")
    return normalized


def _open_absolute_directory(path: Path) -> int:
    _require_posix_open_support()
    descriptor: int | None = None
    try:
        descriptor = os.open(path.anchor, _directory_flags())
        for component in path.parts[1:]:
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            _close_quietly(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise RuntimeRootBindingError("运行时根路径组件必须是目录")
        opened = descriptor
        descriptor = None
        return opened
    except RuntimeRootBindingError:
        raise
    except OSError as error:
        raise RuntimeRootBindingError("运行时根含符号链接或无法安全打开") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _require_secure_root_identity(descriptor: int, owner_uid: int) -> _RuntimeRootIdentity:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise RuntimeRootBindingError("运行时根目录身份无法读取") from error
    mode = int(getattr(metadata, "st_mode", -1))
    uid = int(getattr(metadata, "st_uid", -1))
    if not stat.S_ISDIR(mode):
        raise RuntimeRootBindingError("运行时根路径必须是目录")
    if uid != owner_uid:
        raise RuntimeRootBindingError("运行时根目录属主不安全")
    if stat.S_IMODE(mode) & _UNSAFE_WRITE_BITS:
        raise RuntimeRootBindingError("运行时根目录权限不安全")
    return _RuntimeRootIdentity(
        device=int(getattr(metadata, "st_dev", -1)),
        inode=int(getattr(metadata, "st_ino", -1)),
        owner_uid=uid,
        mode=mode,
    )


def _directory_flags() -> int:
    try:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    except AttributeError as error:
        raise RuntimeRootBindingError("descriptor-bound 运行时根仅支持 POSIX") from error


def _require_posix_open_support() -> None:
    if os.name != "posix" or os.open not in getattr(os, "supports_dir_fd", frozenset()):
        raise RuntimeRootBindingError("descriptor-bound 运行时根仅支持 POSIX")


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


__all__ = [
    "BoundRuntimeRoot",
    "RuntimeRootBinding",
    "RuntimeRootBindingError",
    "validate_runtime_relative_path",
    "validate_runtime_root_path",
]
