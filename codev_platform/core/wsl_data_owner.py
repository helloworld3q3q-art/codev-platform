"""Resolve the WSL distribution that owns an explicitly configured data root."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from codev_platform.core.config import get


_DISTRO_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
_WSL_SERVERS = {"wsl.localhost", "wsl$"}


@dataclass(frozen=True, slots=True)
class WslDataOwner:
    """Stable owner identity derived from a WSL UNC data path."""

    distro: str
    linux_data_root: str


def _parse_wsl_unc(value: object) -> WslDataOwner | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    if normalized.lower().startswith("//?/unc/"):
        normalized = "//" + normalized[8:]
    normalized = normalized.rstrip("/")
    if not normalized.startswith("//"):
        return None
    parts = normalized[2:].split("/")
    if len(parts) < 3 or parts[0].lower() not in _WSL_SERVERS:
        return None
    distro, path_parts = parts[1], parts[2:]
    if (
        not _DISTRO_RE.fullmatch(distro)
        or not path_parts
        or any(not part or part in {".", ".."} for part in path_parts)
    ):
        return None
    return WslDataOwner(distro=distro, linux_data_root="/" + "/".join(path_parts))


def is_wsl_unc_path(value: object) -> bool:
    """Return whether a path-like value names a WSL UNC share."""
    return _parse_wsl_unc(str(value)) is not None


def wsl_data_owner(
    cfg: dict,
    *,
    platform_name: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> WslDataOwner | None:
    """Resolve the WSL owner only for a Windows process with explicit WSL data."""
    runtime = os.name if platform_name is None else platform_name
    if runtime != "nt":
        return None
    selected_environment = os.environ if environment is None else environment
    configured = selected_environment.get("PLATFORM_DATA_DIR") or get(
        cfg,
        "data.platform_data_dir",
    )
    return _parse_wsl_unc(configured)


__all__ = ["WslDataOwner", "is_wsl_unc_path", "wsl_data_owner"]
