"""全量 systemd 安装的受信 manifest 与源文件读取。"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from codev_platform.mcp_systemd_install_contract import (
    SystemdInstallManifest,
    SystemdInstallTransactionError,
    SystemdUnitPayload,
    require_lexical_absolute_path,
)


_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_UNIT_BYTES = 128 * 1024
_READ_CHUNK_BYTES = 8192


@dataclass(frozen=True, slots=True)
class VerifiedInstallInput:
    """从同一个目录句柄读取并由摘要绑定的安装输入快照。"""

    manifest: SystemdInstallManifest
    payloads: tuple[SystemdUnitPayload, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SystemdInstallManifest):
            raise SystemdInstallTransactionError("systemd 安装输入清单无效")
        if tuple(payload.spec for payload in self.payloads) != self.manifest.units:
            raise SystemdInstallTransactionError("systemd 安装输入与清单不一致")


def load_install_manifest(path: Path) -> SystemdInstallManifest:
    """从受控目录句柄读取 manifest，不在路径检查后重新按路径打开。"""
    manifest_path = require_lexical_absolute_path(path, "systemd 安装 manifest 路径")
    directory_descriptor = _open_parent_directory(manifest_path)
    try:
        return _parse_manifest(_read_regular_file_at(
            directory_descriptor,
            manifest_path.name,
            _MAX_MANIFEST_BYTES,
        ))
    finally:
        _close_quietly(directory_descriptor)


def load_verified_install_input(path: Path) -> VerifiedInstallInput:
    """一次目录绑定内读取 manifest 和全部源，并用摘要拒绝并发替换。"""
    manifest_path = require_lexical_absolute_path(path, "systemd 安装 manifest 路径")
    directory_descriptor = _open_parent_directory(manifest_path)
    try:
        manifest = _parse_manifest(_read_regular_file_at(
            directory_descriptor,
            manifest_path.name,
            _MAX_MANIFEST_BYTES,
        ))
        _require_sources_in_manifest_directory(manifest, manifest_path.parent)
        payloads = tuple(
            SystemdUnitPayload(
                spec=unit,
                content=_read_regular_file_at(
                    directory_descriptor,
                    unit.source.name,
                    _MAX_UNIT_BYTES,
                ),
            )
            for unit in manifest.units
        )
        return VerifiedInstallInput(manifest=manifest, payloads=payloads)
    finally:
        _close_quietly(directory_descriptor)


def _parse_manifest(content: bytes) -> SystemdInstallManifest:
    try:
        payload = json.loads(content)
        return SystemdInstallManifest.from_mapping(payload)
    except SystemdInstallTransactionError:
        raise
    except (TypeError, UnicodeError, ValueError) as error:
        raise SystemdInstallTransactionError("systemd 安装 manifest 无法读取") from error


def _require_sources_in_manifest_directory(manifest: SystemdInstallManifest, directory: Path) -> None:
    if any(unit.source.parent != directory for unit in manifest.units):
        raise SystemdInstallTransactionError("systemd unit 源路径必须与 manifest 位于同一目录")


def _open_parent_directory(path: Path) -> int:
    try:
        anchor = str(path.parts[0])
        parents = tuple(str(item) for item in path.parts[1:-1])
    except (IndexError, TypeError, ValueError):
        raise SystemdInstallTransactionError("systemd 安装 manifest 路径无效") from None
    _require_dirfd_support()
    descriptor: int | None = None
    try:
        descriptor = os.open(anchor, _directory_flags())
        for name in parents:
            child = os.open(name, _directory_flags(), dir_fd=descriptor)
            _close_quietly(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SystemdInstallTransactionError("systemd 安装输入目录无效")
        result = descriptor
        descriptor = None
        return result
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except OSError as error:
        raise SystemdInstallTransactionError("systemd 安装输入目录无法安全读取") from error
    except BaseException:
        raise
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _read_regular_file_at(directory_descriptor: int, name: str, max_bytes: int) -> bytes:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise SystemdInstallTransactionError("systemd 安装输入上限无效")
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise SystemdInstallTransactionError("systemd 安装输入文件名无效")
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _regular_file_flags(), dir_fd=directory_descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise SystemdInstallTransactionError("systemd 安装输入文件无效")
        return _read_bounded(descriptor, max_bytes)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except SystemdInstallTransactionError:
        raise
    except OSError as error:
        raise SystemdInstallTransactionError("systemd 安装输入无法安全读取") from error
    finally:
        if descriptor is not None:
            _close_quietly(descriptor)


def _read_bounded(descriptor: int, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = os.read(descriptor, _READ_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise SystemdInstallTransactionError("systemd 安装输入文件超出上限")
        chunks.append(chunk)
    raise SystemdInstallTransactionError("systemd 安装输入文件超出上限")


def _require_dirfd_support() -> None:
    if os.open not in getattr(os, "supports_dir_fd", frozenset()):
        raise SystemdInstallTransactionError("当前平台无法安全读取 systemd 安装输入")


def _directory_flags() -> int:
    return os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW") | _required_flag("O_CLOEXEC")


def _regular_file_flags() -> int:
    return os.O_RDONLY | _required_flag("O_NONBLOCK") | _required_flag("O_NOFOLLOW") | _required_flag("O_CLOEXEC")


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise SystemdInstallTransactionError("当前平台无法安全读取 systemd 安装输入")
    return value


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


__all__ = ["VerifiedInstallInput", "load_install_manifest", "load_verified_install_input"]
