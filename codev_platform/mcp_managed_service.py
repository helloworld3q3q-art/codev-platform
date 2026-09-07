"""MCP HTTP 端点在 systemd 环境中的固定 unit 归属门禁。"""

from __future__ import annotations

import sys
from pathlib import Path

from codev_platform.core.systemd_process_identity import (
    SystemdProcessIdentityError,
    process_belongs_to_systemd_unit,
)
from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind


_SYSTEMD_RUNTIME_DIRECTORY = Path("/run/systemd/system")
_SELF_CGROUP_PATH = Path("/proc/self/cgroup")


class ManagedMCPServiceError(RuntimeError):
    """MCP 端点无法证明自身属于固定 systemd unit。"""


def managed_mcp_service_required() -> bool:
    """仅在可证明无 systemd 时允许兼容的脱离式启动。"""
    if not _is_linux():
        return False
    try:
        _SYSTEMD_RUNTIME_DIRECTORY.lstat()
    except FileNotFoundError:
        return False
    except MemoryError:
        raise
    except Exception:
        return True
    return True


def require_managed_mcp_service(kind: str) -> str:
    """在 systemd 环境证明当前进程精确属于该端点的固定 unit。"""
    try:
        unit = mcp_systemd_unit_for_kind(kind)
    except ValueError as error:
        raise ManagedMCPServiceError("MCP 端点没有固定 systemd unit") from error
    if not managed_mcp_service_required():
        return unit
    try:
        proven = process_belongs_to_systemd_unit(unit, cgroup_path=_SELF_CGROUP_PATH)
    except MemoryError:
        raise
    except SystemdProcessIdentityError as error:
        raise ManagedMCPServiceError("MCP 端点固定 systemd unit 归属无法证明") from error
    if not proven:
        raise ManagedMCPServiceError("MCP 端点不属于固定 systemd unit")
    return unit


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


__all__ = [
    "ManagedMCPServiceError",
    "managed_mcp_service_required",
    "require_managed_mcp_service",
]
