"""稳定运行文件的安全写入边界。"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

_DIRECTORY_MODE = 0o700
_FILE_MODE = 0o600


class RuntimeArtifactIOError(OSError):
    """运行文件无法在既定权限边界内安全写入。"""


def prepare_runtime_artifact_directory(path: Path) -> Path:
    """创建并验证运行文件目录，拒绝符号链接并收紧访问权限。"""
    selected = _absolute_path(path)
    try:
        _prepare_directory_path(selected)
        metadata = selected.lstat()
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeArtifactIOError("运行文件目录不安全")
        if os.name != "nt":
            if metadata.st_uid != os.geteuid():
                raise RuntimeArtifactIOError("运行文件目录所有者不安全")
            selected.chmod(_DIRECTORY_MODE)
            committed = selected.lstat()
            if (
                committed.st_uid != os.geteuid()
                or stat.S_IMODE(committed.st_mode) != _DIRECTORY_MODE
            ):
                raise RuntimeArtifactIOError("运行文件目录权限不安全")
        return selected
    except RuntimeArtifactIOError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeArtifactIOError("运行文件目录无法安全准备") from None


def open_runtime_artifact_binary(path: Path) -> BinaryIO:
    """以追加模式安全打开运行文件，并在返回前提交 0600 权限。"""
    return _open_runtime_artifact_binary(path, mode="ab")


def _open_runtime_artifact_binary(path: Path, *, mode: str) -> BinaryIO:
    selected = _absolute_path(path)
    prepare_runtime_artifact_directory(selected.parent)
    before = _existing_regular_metadata(selected, label="运行文件")

    def opener(raw_path: str, flags: int) -> int:
        secure_flags = (
            flags
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | (getattr(os, "O_NONBLOCK", 0) if os.name != "nt" else 0)
        )
        return os.open(raw_path, secure_flags, _FILE_MODE)

    stream: BinaryIO | None = None
    try:
        stream = open(selected, mode, buffering=0, opener=opener)
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeArtifactIOError("运行文件类型不安全")
        linked = _existing_regular_metadata(selected, label="运行文件")
        if linked is None or not _same_opened_path(metadata, linked):
            raise RuntimeArtifactIOError("运行文件路径身份不安全")
        if before is not None and not _same_opened_path(before, linked):
            raise RuntimeArtifactIOError("运行文件路径发生变化")
        if os.name != "nt":
            os.set_blocking(stream.fileno(), True)
        if os.name != "nt":
            if metadata.st_uid != os.geteuid():
                raise RuntimeArtifactIOError("运行文件所有者不安全")
            os.fchmod(stream.fileno(), _FILE_MODE)
            committed = os.fstat(stream.fileno())
            if committed.st_uid != os.geteuid() or stat.S_IMODE(committed.st_mode) != _FILE_MODE:
                raise RuntimeArtifactIOError("运行文件权限不安全")
        return stream
    except RuntimeArtifactIOError:
        if stream is not None:
            stream.close()
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        if stream is not None:
            stream.close()
        raise RuntimeArtifactIOError("运行文件无法安全打开") from None


def append_runtime_artifact_text(path: Path, text: str) -> bool:
    """尽力追加 UTF-8 文本；失败转写固定告警到 stderr，不影响主请求。"""
    try:
        if type(text) is not str:
            raise TypeError("运行文件文本类型无效")
        with open_runtime_artifact_binary(path) as stream:
            _write_all(stream, text.encode("utf-8", "strict"))
        return True
    except (OSError, RuntimeError, TypeError, UnicodeError):
        try:
            print("[codev-platform] 运行观测文件写入失败", file=sys.stderr, flush=True)
        except Exception:
            pass
        return False


def write_runtime_artifact_text(path: Path, text: str) -> bool:
    """以同目录临时文件原子覆盖 UTF-8 快照，失败保留旧版本。"""
    try:
        if type(text) is not str:
            raise TypeError("运行文件文本类型无效")
        _atomic_write_runtime_artifact(path, text.encode("utf-8", "strict"))
        return True
    except (OSError, RuntimeError, TypeError, UnicodeError):
        try:
            print("[codev-platform] 运行观测文件写入失败", file=sys.stderr, flush=True)
        except Exception:
            pass
        return False


def write_runtime_artifact_bytes(path: Path, content: bytes) -> bool:
    """以同目录临时文件原子覆盖字节快照，失败保留旧版本。"""
    try:
        if type(content) is not bytes:
            raise TypeError("运行文件字节类型无效")
        _atomic_write_runtime_artifact(path, content)
        return True
    except (OSError, RuntimeError, TypeError):
        try:
            print("[codev-platform] 运行观测文件写入失败", file=sys.stderr, flush=True)
        except Exception:
            pass
        return False


def _atomic_write_runtime_artifact(path: Path, content: bytes) -> None:
    selected = _absolute_path(path)
    directory = prepare_runtime_artifact_directory(selected.parent)
    _verify_existing_target(selected)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=f".{selected.name}.",
            suffix=".tmp",
            dir=directory,
        )
        temporary = Path(raw_temporary)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeArtifactIOError("运行文件临时对象类型不安全")
        if os.name != "nt":
            if metadata.st_uid != os.geteuid():
                raise RuntimeArtifactIOError("运行文件临时对象所有者不安全")
            os.fchmod(descriptor, _FILE_MODE)
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            descriptor = -1
            _write_all(stream, content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, selected)
        temporary = None
        _fsync_directory(directory)
    except RuntimeArtifactIOError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError):
        raise RuntimeArtifactIOError("运行文件原子覆盖失败") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _verify_existing_target(path: Path) -> None:
    before = _existing_regular_metadata(path, label="既有运行文件")
    if before is None:
        return
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | (getattr(os, "O_NONBLOCK", 0) if os.name != "nt" else 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RuntimeArtifactIOError("既有运行文件不安全") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise RuntimeArtifactIOError("既有运行文件类型不安全")
        linked = _existing_regular_metadata(path, label="既有运行文件")
        if (
            linked is None
            or not _same_opened_path(before, linked)
            or not _same_opened_path(metadata, linked)
        ):
            raise RuntimeArtifactIOError("既有运行文件路径身份不安全")
        if os.name != "nt" and metadata.st_uid != os.geteuid():
            raise RuntimeArtifactIOError("既有运行文件所有者不安全")
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(stream: BinaryIO, content: bytes) -> None:
    """处理底层短写，确保一次逻辑记录完整提交。"""
    remaining = memoryview(content)
    while remaining:
        written = stream.write(remaining)
        if type(written) is not int or written <= 0 or written > len(remaining):
            raise RuntimeArtifactIOError("运行文件写入不完整")
        remaining = remaining[written:]


def _prepare_directory_path(path: Path) -> None:
    missing: list[Path] = []
    current = path
    while True:
        try:
            current.lstat()
            break
        except FileNotFoundError:
            if current == current.parent:
                raise RuntimeArtifactIOError("运行文件目录无法安全准备") from None
            missing.append(current)
            current = current.parent
        except OSError:
            raise RuntimeArtifactIOError("运行文件目录无法安全准备") from None
    _require_plain_directory_chain(current)
    for candidate in reversed(missing):
        try:
            candidate.mkdir(mode=_DIRECTORY_MODE)
        except FileExistsError:
            pass
        metadata = candidate.lstat()
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeArtifactIOError("运行文件目录不安全")
        if os.name != "nt":
            if metadata.st_uid != os.geteuid():
                raise RuntimeArtifactIOError("运行文件目录所有者不安全")
            candidate.chmod(_DIRECTORY_MODE)
    _require_plain_directory_chain(path)


def _require_plain_directory_chain(path: Path) -> None:
    current = path
    while True:
        metadata = current.lstat()
        if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise RuntimeArtifactIOError("运行文件目录链不安全")
        if current == current.parent:
            return
        current = current.parent


def _existing_regular_metadata(path: Path, *, label: str) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeArtifactIOError(f"{label}不可安全检查") from None
    if (
        _is_link_or_reparse(metadata)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise RuntimeArtifactIOError(f"{label}类型不安全")
    return metadata


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _same_opened_path(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        stat.S_IFMT(before.st_mode),
        before.st_dev,
        before.st_ino,
        before.st_nlink,
    ) == (
        stat.S_IFMT(after.st_mode),
        after.st_dev,
        after.st_ino,
        after.st_nlink,
    )


def _absolute_path(path: Path) -> Path:
    selected = Path(path)
    if not selected.is_absolute() or selected != Path(os.path.abspath(selected)):
        raise RuntimeArtifactIOError("运行文件路径必须是绝对路径")
    return selected


__all__ = [
    "RuntimeArtifactIOError",
    "append_runtime_artifact_text",
    "open_runtime_artifact_binary",
    "prepare_runtime_artifact_directory",
    "write_runtime_artifact_bytes",
    "write_runtime_artifact_text",
]
