"""生产 base 使用的离线 wheelhouse 完整性证明。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from codev_platform.runtime_dependency_contract import RequirementsLockContract
from codev_platform.runtime_wheel_tags import (
    RuntimeWheelCompatibilityError,
    require_compatible_wheel,
)


_MAX_WHEEL_BYTES = 4 * 1024 * 1024 * 1024
_MAX_WHEELHOUSE_BYTES = 16 * 1024 * 1024 * 1024


class RuntimeWheelhouseError(RuntimeError):
    """wheelhouse 不能与 hash lock 形成一一对应的可信快照。"""


def verify_locked_wheelhouse(
    contract: RequirementsLockContract,
    wheelhouse: Path,
) -> Path:
    """验证目录中恰好存在 lock 声明的普通 wheel 且摘要完全一致。"""
    if not isinstance(contract, RequirementsLockContract):
        raise TypeError("依赖锁契约无效")
    root = _trusted_directory(Path(wheelhouse))
    expected = {artifact.filename: artifact for artifact in contract.artifacts}
    try:
        entries = tuple(root.iterdir())
    except OSError:
        raise RuntimeWheelhouseError("离线 wheelhouse 不可枚举") from None
    if {entry.name for entry in entries} != set(expected):
        raise RuntimeWheelhouseError("离线 wheelhouse 与依赖锁制品集合不一致")
    pins = {pin.name: pin for pin in contract.pins}
    total = 0
    for entry in entries:
        artifact = expected[entry.name]
        try:
            require_compatible_wheel(
                artifact.filename,
                package=artifact.package,
                version=pins[artifact.package].version,
            )
        except RuntimeWheelCompatibilityError as error:
            raise RuntimeWheelhouseError(str(error)) from None
        metadata = _regular_wheel(entry)
        total += metadata.st_size
        if total > _MAX_WHEELHOUSE_BYTES:
            raise RuntimeWheelhouseError("离线 wheelhouse 总量超过安全预算")
        if _sha256(entry) != artifact.sha256:
            raise RuntimeWheelhouseError("离线 wheelhouse 制品摘要不一致")
    return root


def _trusted_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise RuntimeWheelhouseError("离线 wheelhouse 必须是绝对路径")
    try:
        metadata = path.lstat()
        root = path.resolve(strict=True)
    except OSError:
        raise RuntimeWheelhouseError("离线 wheelhouse 不可用") from None
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or root != path
    ):
        raise RuntimeWheelhouseError("离线 wheelhouse 路径不受信任")
    if os.name == "posix" and os.geteuid() == 0:
        _require_root_owned_chain(root)
    return root


def _require_root_owned_chain(path: Path) -> None:
    current = path
    while True:
        metadata = current.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise RuntimeWheelhouseError("离线 wheelhouse 目录权限不受信任")
        if current == current.parent:
            return
        current = current.parent


def _regular_wheel(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError:
        raise RuntimeWheelhouseError("离线 wheel 制品不可用") from None
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > _MAX_WHEEL_BYTES
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022)
        or (os.name == "posix" and os.geteuid() == 0 and metadata.st_uid != 0)
    ):
        raise RuntimeWheelhouseError("离线 wheel 制品元数据不受信任")
    return metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        raise RuntimeWheelhouseError("离线 wheel 制品不可读取") from None
    return digest.hexdigest()


__all__ = ["RuntimeWheelhouseError", "verify_locked_wheelhouse"]
