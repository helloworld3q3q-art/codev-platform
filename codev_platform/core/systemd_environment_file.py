"""systemd ``EnvironmentFile=`` 固定路径的共享词法契约。"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from pathlib import PurePosixPath


_SYSTEMD_SYNTAX_CHARACTERS = frozenset("*?[]'\"\\%")
_ASSIGNMENT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=")
_RESERVED_KEYS = frozenset(
    {
        "CODEV_PLATFORM_RELEASE_FILE",
        "CODEV_REINDEX_CONFIG_DIGEST",
        "CODEV_REINDEX_EXPECTED_RUNTIME_REVISION",
        "CODEV_REINDEX_REPO_OVERRIDE",
        "GLIBC_TUNABLES",
        "HOME",
        "INVOCATION_ID",
        "JOURNAL_STREAM",
        "LOGNAME",
        "NOTIFY_SOCKET",
        "PATH",
        "SHELL",
        "SYSTEMD_EXEC_PID",
        "USER",
        "VIRTUAL_ENV",
    }
)
_RESERVED_PREFIXES = ("LD_", "LISTEN_", "PYTHON", "WATCHDOG_")
_MAX_ENVIRONMENT_FILE_BYTES = 128 * 1024


class SystemdEnvironmentFilePathError(ValueError):
    """环境文件路径不能安全地作为 systemd 指令字面量。"""


class SystemdEnvironmentFileError(RuntimeError):
    """环境文件的所有权、格式或变量边界不受信任。"""


def require_systemd_environment_file_path(value: object) -> str:
    """返回未经二次解释的 POSIX 绝对路径，否则失败关闭。"""
    if type(value) is not str or not value:
        raise SystemdEnvironmentFilePathError("systemd.env_file 必须是固定的 POSIX 绝对路径")
    try:
        path = PurePosixPath(value)
    except (TypeError, ValueError):
        raise SystemdEnvironmentFilePathError(
            "systemd.env_file 必须是固定的 POSIX 绝对路径"
        ) from None
    components = value.split("/")[1:]
    if (
        not path.is_absolute()
        or len(path.parts) < 2
        or value.startswith("//")
        or "//" in value
        or path.as_posix() != value
        or any(component in {"", ".", ".."} for component in components)
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
        or any(symbol in value for symbol in _SYSTEMD_SYNTAX_CHARACTERS)
    ):
        raise SystemdEnvironmentFilePathError("systemd.env_file 必须是固定的 POSIX 绝对路径")
    return value


def parse_systemd_environment_keys(content: bytes) -> frozenset[str]:
    """只解析赋值名称，不解码或回显可能含秘密的值。"""
    if type(content) is not bytes or len(content) > _MAX_ENVIRONMENT_FILE_BYTES:
        raise SystemdEnvironmentFileError("systemd 环境文件大小无效")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise SystemdEnvironmentFileError("systemd 环境文件编码无效") from None
    if "\x00" in text:
        raise SystemdEnvironmentFileError("systemd 环境文件包含无效字符")
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            continue
        if stripped.endswith("\\"):
            raise SystemdEnvironmentFileError("systemd 环境文件不允许续行")
        match = _ASSIGNMENT.match(stripped)
        if match is None:
            raise SystemdEnvironmentFileError("systemd 环境文件赋值格式无效")
        key = match.group(1)
        if key in keys:
            raise SystemdEnvironmentFileError("systemd 环境文件包含重复变量")
        if key in _RESERVED_KEYS or key.startswith(_RESERVED_PREFIXES):
            raise SystemdEnvironmentFileError("systemd 环境文件覆盖了保留变量")
        keys.add(key)
    return frozenset(keys)


def validate_systemd_environment_file(path: Path) -> frozenset[str]:
    """验证 root 受管路径链、稳定普通文件及保留变量。"""
    selected = Path(path)
    if not selected.is_absolute() or Path(os.path.abspath(selected)) != selected:
        raise SystemdEnvironmentFileError("systemd 环境文件路径不受信任")
    _validate_root_owned_directory_chain(selected.parent)
    try:
        metadata = selected.lstat()
    except OSError as error:
        raise SystemdEnvironmentFileError("systemd 环境文件不可用") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_uid", -1) != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or metadata.st_size > _MAX_ENVIRONMENT_FILE_BYTES
        or metadata.st_nlink != 1
    ):
        raise SystemdEnvironmentFileError("systemd 环境文件元数据不受信任")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected, flags)
    except OSError as error:
        raise SystemdEnvironmentFileError("systemd 环境文件不可安全打开") from error
    try:
        opened = os.fstat(descriptor)
        if not _same_environment_file(metadata, opened):
            raise SystemdEnvironmentFileError("systemd 环境文件读取期间发生变化")
        content = _read_bounded_environment_file(descriptor)
        after = os.fstat(descriptor)
        if not _same_environment_file(opened, after) or len(content) != after.st_size:
            raise SystemdEnvironmentFileError("systemd 环境文件读取期间发生变化")
    finally:
        os.close(descriptor)
    return parse_systemd_environment_keys(content)


def _validate_root_owned_directory_chain(directory: Path) -> None:
    """拒绝任一可由非 root 替换环境文件的祖先目录。"""
    current = directory
    while True:
        try:
            metadata = current.lstat()
        except OSError as error:
            raise SystemdEnvironmentFileError("systemd 环境文件目录不可用") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_uid", -1) != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise SystemdEnvironmentFileError("systemd 环境文件目录不受信任")
        if current == current.parent:
            return
        current = current.parent


def _read_bounded_environment_file(descriptor: int) -> bytes:
    blocks: list[bytes] = []
    total = 0
    while True:
        block = os.read(
            descriptor,
            min(64 * 1024, _MAX_ENVIRONMENT_FILE_BYTES + 1 - total),
        )
        if not block:
            return b"".join(blocks)
        blocks.append(block)
        total += len(block)
        if total > _MAX_ENVIRONMENT_FILE_BYTES:
            raise SystemdEnvironmentFileError("systemd 环境文件大小无效")


def _same_environment_file(
    before: os.stat_result,
    after: os.stat_result,
) -> bool:
    return (
        stat.S_ISREG(after.st_mode)
        and not stat.S_ISLNK(after.st_mode)
        and after.st_uid == 0
        and stat.S_IMODE(after.st_mode) & 0o022 == 0
        and after.st_nlink == 1
        and after.st_size <= _MAX_ENVIRONMENT_FILE_BYTES
        and (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
            after.st_gid,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
        )
        == (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
        )
    )


__all__ = [
    "SystemdEnvironmentFilePathError",
    "SystemdEnvironmentFileError",
    "parse_systemd_environment_keys",
    "require_systemd_environment_file_path",
    "validate_systemd_environment_file",
]
