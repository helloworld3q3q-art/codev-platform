"""运行时受管文件的 POSIX dirfd 机械层与旧 root 文件兼容实现。"""
from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

_ROOT_UID = 0
_UNSAFE_WRITE_BITS = stat.S_IWGRP | stat.S_IWOTH
_CREATED_DIRECTORY_MODE = 0o755
_TEMPORARY_ATTEMPTS = 8
_TEMPORARY_FILE_MODE = 0o600
_READ_CHUNK_BYTES = 8192


class ManagedFileError(RuntimeError):
    """受管文件 descriptor-safe 操作失败。"""


class TrustedManagedPathError(ManagedFileError):
    """受管路径无法以 root 可信 dirfd 链安全操作。"""


@dataclass(frozen=True, slots=True)
class RootOwnedRegularFileSnapshot:
    """同一受信描述符读取的文件内容、权限、属主与可选 inode 身份。"""

    content: bytes
    mode: int
    uid: int
    gid: int
    device: int = 0
    inode: int = 0

    def __post_init__(self) -> None:
        _require_root_owned_snapshot_values(self.content, self.mode, self.uid, self.gid)
        if (
            type(self.device) is not int
            or type(self.inode) is not int
            or self.device < 0
            or self.inode < 0
        ):
            raise TrustedManagedPathError("受管路径原像身份无效")

    def matches(self, expected: RootOwnedRegularFileSnapshot, *, require_identity: bool) -> bool:
        """比较内容元数据；有身份时额外证明仍是同一个叶子 inode。"""
        if not isinstance(expected, RootOwnedRegularFileSnapshot):
            return False
        if (self.content, self.mode, self.uid, self.gid) != (
            expected.content,
            expected.mode,
            expected.uid,
            expected.gid,
        ):
            return False
        if not require_identity:
            return True
        return self.device == expected.device and self.inode == expected.inode


def read_optional_root_owned_regular_file(path: Path, *, max_bytes: int) -> bytes | None:
    """从同一可信父目录句柄读取可选 root 文件，不存在时返回 ``None``。"""
    snapshot = read_optional_root_owned_regular_file_snapshot(path, max_bytes=max_bytes)
    return None if snapshot is None else snapshot.content


def read_optional_root_owned_regular_file_snapshot(
    path: Path,
    *,
    max_bytes: int,
) -> RootOwnedRegularFileSnapshot | None:
    """从同一受信描述符读取可选文件的内容、权限和完整属主。"""
    _require_max_bytes(max_bytes)
    flags = _read_file_flags()
    parent = _open_trusted_parent(path, create_missing=False)
    if parent is None:
        return None
    parent_descriptor, leaf = parent
    descriptor: int | None = None
    try:
        descriptor = _open_optional_regular_file(leaf, parent_descriptor, flags)
        if descriptor is None:
            return None
        mode, uid, gid, device, inode = _root_owned_regular_file_metadata(_fstat(descriptor))
        return RootOwnedRegularFileSnapshot(
            content=_read_bounded(descriptor, max_bytes),
            mode=mode,
            uid=uid,
            gid=gid,
            device=device,
            inode=inode,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TrustedManagedPathError:
        raise
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全读取") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        _close_quietly(parent_descriptor)


def write_root_owned_regular_file_atomic(
    path: Path,
    content: bytes,
    *,
    mode: int,
    uid: int = _ROOT_UID,
    gid: int = 0,
) -> None:
    """只在可信父 dirfd 下创建临时文件、替换叶子并同步目录。"""
    _require_write_arguments(content, mode, uid, gid)
    if not _supports_write():
        raise TrustedManagedPathError("受管路径无法安全原子写入")
    _temporary_file_flags()
    parent_descriptor: int | None = None
    temporary_descriptor: int | None = None
    temporary_name: str | None = None
    replaced = False
    try:
        parent_descriptor, leaf = _open_trusted_parent(path, create_missing=True)
        temporary_name, temporary_descriptor = _open_temporary_file(parent_descriptor, mode)
        _write_all(temporary_descriptor, content)
        _fchown(temporary_descriptor, uid, gid)
        _fchmod(temporary_descriptor, mode)
        _fsync(temporary_descriptor)
        _replace_at(temporary_name, leaf, parent_descriptor, parent_descriptor)
        replaced = True
        _fsync(parent_descriptor)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TrustedManagedPathError:
        raise
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全原子写入") from error
    finally:
        if temporary_descriptor is not None:
            _close_quietly(temporary_descriptor)
        if temporary_name is not None and not replaced and parent_descriptor is not None:
            _unlink_temporary_quietly(temporary_name, parent_descriptor)
        if parent_descriptor is not None:
            _close_quietly(parent_descriptor)


def remove_root_owned_regular_file(path: Path) -> bool:
    """仅删除经同一可信父 dirfd 验证的 root 常规文件。"""
    if not _supports_remove():
        raise TrustedManagedPathError("受管路径无法安全删除")
    flags = _read_file_flags()
    parent = _open_trusted_parent(path, create_missing=False)
    if parent is None:
        return False
    parent_descriptor, leaf = parent
    descriptor: int | None = None
    try:
        descriptor = _open_optional_regular_file(leaf, parent_descriptor, flags)
        if descriptor is None:
            return False
        _require_root_owned_regular_file(_fstat(descriptor))
        _close_quietly(descriptor)
        descriptor = None
        _unlink_at(leaf, parent_descriptor)
        _fsync(parent_descriptor)
        return True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except TrustedManagedPathError:
        raise
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全删除") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)
        _close_quietly(parent_descriptor)


def _open_trusted_parent(path: Path, *, create_missing: bool) -> tuple[int, str] | None:
    anchor, parents, leaf = _split_absolute_path(path)
    descriptor: int | None = None
    try:
        descriptor = _open_directory(anchor)
        _require_trusted_directory(_fstat(descriptor))
        for name in parents:
            child = _open_or_create_trusted_directory(
                name,
                descriptor,
                create_missing=create_missing,
            )
            if child is None:
                _close_quietly(descriptor)
                descriptor = None
                return None
            _close_quietly(descriptor)
            descriptor = child
        return descriptor, leaf
    except BaseException:
        if descriptor is not None:
            _close_quietly(descriptor)
        raise


def _open_or_create_trusted_directory(
    name: str,
    parent_descriptor: int,
    *,
    create_missing: bool,
) -> int | None:
    try:
        descriptor = _open_directory(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        if not create_missing:
            return None
        descriptor = _create_trusted_directory(name, parent_descriptor)
    try:
        _require_trusted_directory(_fstat(descriptor))
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _create_trusted_directory(name: str, parent_descriptor: int) -> int:
    try:
        _mkdir_at(name, _CREATED_DIRECTORY_MODE, parent_descriptor)
    except FileExistsError:
        pass
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全创建父目录") from error
    try:
        descriptor = _open_directory(name, dir_fd=parent_descriptor)
    except FileNotFoundError as error:
        raise TrustedManagedPathError("受管路径无法安全创建父目录") from error
    try:
        _require_trusted_directory(_fstat(descriptor))
        _fchmod(descriptor, _CREATED_DIRECTORY_MODE)
        _fsync(descriptor)
        _fsync(parent_descriptor)
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _split_absolute_path(value: Path) -> tuple[str, tuple[str, ...], str]:
    try:
        path = Path(value)
        if not path.is_absolute():
            raise TrustedManagedPathError("受管路径必须为绝对路径")
        parts = path.parts
    except TrustedManagedPathError:
        raise
    except (TypeError, ValueError):
        raise TrustedManagedPathError("受管路径无法安全操作") from None
    if len(parts) < 2 or not path.anchor:
        raise TrustedManagedPathError("受管路径无法安全操作")
    anchor = str(parts[0])
    components = tuple(str(item) for item in parts[1:])
    if not components or any(
        item in {"", ".", ".."} or "\x00" in item for item in components
    ):
        raise TrustedManagedPathError("受管路径无法安全操作")
    return anchor, components[:-1], components[-1]


def _open_directory(name: str, *, dir_fd: int | None = None) -> int:
    _require_open_support()
    try:
        return _open_at(name, _directory_flags(), dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全打开") from error


def _open_optional_regular_file(name: str, parent_descriptor: int, flags: int) -> int | None:
    _require_open_support()
    try:
        return _open_at(name, flags, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise TrustedManagedPathError("受管路径无法安全打开") from error


def _open_temporary_file(parent_descriptor: int, _mode: int) -> tuple[str, int]:
    """临时载荷在写完并 fchmod 前始终只允许属主访问。"""
    flags = _temporary_file_flags()
    for _attempt in range(_TEMPORARY_ATTEMPTS):
        name = _temporary_name()
        try:
            return name, _open_at(
                name,
                flags,
                dir_fd=parent_descriptor,
                mode=_TEMPORARY_FILE_MODE,
            )
        except FileExistsError:
            continue
        except OSError as error:
            raise TrustedManagedPathError("受管路径无法安全创建临时文件") from error
    raise TrustedManagedPathError("受管路径无法安全创建临时文件")


def _require_open_support() -> None:
    if not _supports_read():
        raise TrustedManagedPathError("受管路径无法安全打开")


def _supports_read() -> bool:
    return _supports_dir_fd(os.open)


def _supports_write() -> bool:
    return all(_supports_dir_fd(item) for item in (os.open, os.mkdir, os.rename, os.unlink)) and callable(
        getattr(os, "fchown", None)
    )


def _supports_remove() -> bool:
    return all(_supports_dir_fd(item) for item in (os.open, os.unlink))


def _supports_dir_fd(function: object) -> bool:
    return function in getattr(os, "supports_dir_fd", frozenset())


def _directory_flags() -> int:
    return os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW") | _close_on_exec_flag()


def _read_file_flags() -> int:
    return (
        os.O_RDONLY
        | _required_flag("O_NONBLOCK")
        | _required_flag("O_NOFOLLOW")
        | _close_on_exec_flag()
    )


def _temporary_file_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | _required_flag("O_NOFOLLOW")
        | _close_on_exec_flag()
    )


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise TrustedManagedPathError("受管路径无法安全打开")
    return value


def _close_on_exec_flag() -> int:
    return _required_flag("O_CLOEXEC")


def _require_trusted_directory(metadata: object) -> None:
    mode = getattr(metadata, "st_mode", None)
    owner = getattr(metadata, "st_uid", None)
    if (
        type(mode) is not int
        or type(owner) is not int
        or not stat.S_ISDIR(mode)
        or owner != _ROOT_UID
        or mode & _UNSAFE_WRITE_BITS
    ):
        raise TrustedManagedPathError("受管路径父目录不受信任")


def _require_root_owned_regular_file(metadata: object) -> None:
    _root_owned_regular_file_metadata(metadata)


def _root_owned_regular_file_metadata(metadata: object) -> tuple[int, int, int, int, int]:
    mode = getattr(metadata, "st_mode", None)
    owner = getattr(metadata, "st_uid", None)
    group = getattr(metadata, "st_gid", None)
    links = getattr(metadata, "st_nlink", None)
    device = getattr(metadata, "st_dev", 0)
    inode = getattr(metadata, "st_ino", 0)
    if (
        type(mode) is not int
        or type(owner) is not int
        or type(group) is not int
        or type(links) is not int
        or type(device) is not int
        or type(inode) is not int
        or not stat.S_ISREG(mode)
        or owner != _ROOT_UID
        or group < 0
        or links != 1
        or device < 0
        or inode < 0
        or mode & _UNSAFE_WRITE_BITS
    ):
        raise TrustedManagedPathError("受管路径文件不受信任")
    return stat.S_IMODE(mode), owner, group, device, inode


def _require_max_bytes(max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes < 0:
        raise TrustedManagedPathError("受管路径读取上限无效")


def _require_write_arguments(content: bytes, mode: int, uid: int, gid: int) -> None:
    _require_root_owned_snapshot_values(content, mode, uid, gid)


def _require_root_owned_snapshot_values(content: bytes, mode: int, uid: int, gid: int) -> None:
    if (
        type(content) is not bytes
        or type(mode) is not int
        or type(uid) is not int
        or type(gid) is not int
        or not 0 <= mode <= 0o777
        or uid != _ROOT_UID
        or gid < 0
    ):
        raise TrustedManagedPathError("受管路径写入参数无效")
    if mode & _UNSAFE_WRITE_BITS:
        raise TrustedManagedPathError("受管路径写入权限不安全")


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        try:
            chunk = _read(descriptor, _READ_CHUNK_BYTES)
        except OSError as error:
            raise TrustedManagedPathError("受管路径无法安全读取") from error
        if type(chunk) is not bytes:
            raise TrustedManagedPathError("受管路径无法安全读取")
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise TrustedManagedPathError("受管路径读取超出上限")
        chunks.append(chunk)
    raise TrustedManagedPathError("受管路径读取超出上限")


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = _write(descriptor, content[offset:])
        if type(written) is not int or written <= 0:
            raise TrustedManagedPathError("受管路径原子写入未取得进展")
        offset += written


def _temporary_name() -> str:
    return f".codev-reindex-{secrets.token_hex(16)}.tmp"


def _unlink_temporary_quietly(name: str, parent_descriptor: int) -> None:
    try:
        _unlink_at(name, parent_descriptor)
    except OSError:
        pass


def _open_at(name: str, flags: int, *, dir_fd: int | None = None, mode: int = 0o777) -> int:
    if dir_fd is None:
        return os.open(name, flags, mode)
    return os.open(name, flags, mode, dir_fd=dir_fd)


def _mkdir_at(name: str, mode: int, parent_descriptor: int) -> None:
    os.mkdir(name, mode, dir_fd=parent_descriptor)


def _replace_at(source: str, destination: str, source_parent: int, destination_parent: int) -> None:
    os.rename(source, destination, src_dir_fd=source_parent, dst_dir_fd=destination_parent)


def _unlink_at(name: str, parent_descriptor: int) -> None:
    os.unlink(name, dir_fd=parent_descriptor)


def _fstat(descriptor: int) -> object:
    return os.fstat(descriptor)


def _read(descriptor: int, size: int) -> bytes:
    return os.read(descriptor, size)


def _write(descriptor: int, content: bytes) -> int:
    return os.write(descriptor, content)


def _fchmod(descriptor: int, mode: int) -> None:
    os.fchmod(descriptor, mode)


def _fchown(descriptor: int, uid: int, gid: int) -> None:
    os.fchown(descriptor, uid, gid)


def _fsync(descriptor: int) -> None:
    os.fsync(descriptor)


def _close(descriptor: int) -> None:
    os.close(descriptor)


def _close_quietly(descriptor: int) -> None:
    try:
        _close(descriptor)
    except OSError:
        pass


__all__ = [
    "RootOwnedRegularFileSnapshot",
    "TrustedManagedPathError",
    "read_optional_root_owned_regular_file",
    "read_optional_root_owned_regular_file_snapshot",
    "remove_root_owned_regular_file",
    "write_root_owned_regular_file_atomic",
]
