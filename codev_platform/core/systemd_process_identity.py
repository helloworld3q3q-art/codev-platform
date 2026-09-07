"""systemd 进程 cgroup 归属的严格解析与证明。"""

from __future__ import annotations

import re
from pathlib import Path


_SYSTEMD_SERVICE_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]*\.service\Z")
_MAX_CGROUP_BYTES = 16 * 1024


class SystemdProcessIdentityError(RuntimeError):
    """cgroup 原像无法证明进程属于目标 systemd unit。"""


def process_belongs_to_systemd_unit(
    unit: str,
    *,
    cgroup_path: Path,
) -> bool:
    """严格读取指定进程的 cgroup，仅对精确 unit 或其子 cgroup 返回真。"""
    path = Path(cgroup_path)
    if not path.is_absolute():
        raise SystemdProcessIdentityError("systemd 进程 cgroup 路径无效")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SystemdProcessIdentityError("systemd 进程 cgroup 无法读取") from error
    return cgroup_belongs_to_systemd_unit(raw, unit)


def cgroup_belongs_to_systemd_unit(raw: bytes, unit: str) -> bool:
    """解析 cgroup v1/v2 原像，不接受宽泛子串命中。"""
    target = _systemd_unit_cgroup_path(unit)
    return any(path == target or path.startswith(f"{target}/") for path in _cgroup_paths(raw))


def cgroup_belongs_to_unit(raw: bytes, unit: str) -> bool:
    """证明 cgroup 路径含精确 systemd unit 片段，兼容 user.slice 与 system.slice。"""
    _require_unit(unit)
    return any(unit in path[1:].split("/") for path in _cgroup_paths(raw))


def _cgroup_paths(raw: bytes) -> tuple[str, ...]:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_CGROUP_BYTES:
        raise SystemdProcessIdentityError("systemd 进程 cgroup 原像无效")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise SystemdProcessIdentityError("systemd 进程 cgroup 编码无效") from None
    lines = tuple(line for line in text.splitlines() if line)
    if not lines:
        raise SystemdProcessIdentityError("systemd 进程 cgroup 格式无效")
    return tuple(_cgroup_path(line) for line in lines)


def _cgroup_path(line: str) -> str:
    fields = line.split(":", 2)
    if len(fields) != 3:
        raise SystemdProcessIdentityError("systemd 进程 cgroup 格式无效")
    hierarchy, controllers, path = fields
    if (
        not hierarchy.isascii()
        or not hierarchy.isdigit()
        or "\x00" in controllers
        or not path.startswith("/")
        or "\x00" in path
        or _unsafe_cgroup_path(path)
    ):
        raise SystemdProcessIdentityError("systemd 进程 cgroup 格式无效")
    return path


def _unsafe_cgroup_path(path: str) -> bool:
    if path == "/":
        return False
    segments = path[1:].split("/")
    return any(
        segment in {"", ".", ".."}
        or any(ord(character) < 32 or ord(character) == 127 for character in segment)
        for segment in segments
    )


def _systemd_unit_cgroup_path(unit: str) -> str:
    _require_unit(unit)
    return f"/system.slice/{unit}"


def _require_unit(unit: str) -> None:
    if type(unit) is not str or _SYSTEMD_SERVICE_UNIT.fullmatch(unit) is None:
        raise SystemdProcessIdentityError("systemd service unit 名称无效")


__all__ = [
    "SystemdProcessIdentityError",
    "cgroup_belongs_to_unit",
    "cgroup_belongs_to_systemd_unit",
    "process_belongs_to_systemd_unit",
]
