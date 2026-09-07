"""独立于 runtime mask 的受管 systemd 持久启用链接证明。"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from codev_platform.mcp_systemd_install_contract import SystemdInstallTransactionError
from codev_platform.mcp_systemd_unit_registry import managed_systemd_unit


_SYSTEMD_CONFIG_ROOT = Path("/etc/systemd/system")
Lstat = Callable[[Path], os.stat_result]
Readlink = Callable[[Path], str]


class ManagedSystemdEnablementError(SystemdInstallTransactionError):
    """受管 unit 的持久启用链接无法精确证明。"""


def read_persistent_unit_enablement_state(
    name: str,
    *,
    lstat: Lstat = os.lstat,
    readlink: Readlink = os.readlink,
) -> str:
    """返回 enabled/disabled；链接存在但不受信任时失败关闭。"""
    registration = _registration(name)
    target = registration.enable_target
    if type(target) is not str:
        raise ManagedSystemdEnablementError("systemd unit 不支持持久启用")
    link = _SYSTEMD_CONFIG_ROOT / f"{target}.wants" / name
    try:
        metadata = lstat(link)
    except FileNotFoundError:
        return "disabled"
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ManagedSystemdEnablementError("systemd 持久启用链接无法读取") from None
    if (
        type(getattr(metadata, "st_mode", None)) is not int
        or not stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_uid", None) != 0
        or getattr(metadata, "st_gid", None) != 0
    ):
        raise ManagedSystemdEnablementError("systemd 持久启用链接不受信任")
    try:
        actual = readlink(link)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise ManagedSystemdEnablementError("systemd 持久启用链接无法读取") from None
    if type(actual) is not str or actual != registration.destination.as_posix():
        raise ManagedSystemdEnablementError("systemd 持久启用链接目标不匹配")
    return "enabled"


def _registration(name: object):
    if type(name) is not str:
        raise ManagedSystemdEnablementError("systemd unit 名称无效")
    try:
        registration = managed_systemd_unit(name)
    except ValueError:
        raise ManagedSystemdEnablementError("systemd unit 不在受管注册表中") from None
    if registration.enable is not True:
        raise ManagedSystemdEnablementError("systemd unit 不支持持久启用")
    return registration


__all__ = [
    "ManagedSystemdEnablementError",
    "read_persistent_unit_enablement_state",
]
