"""运行时服务访问入口的值与平台边界验证。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from codev_platform._runtime_service_access_contracts import RuntimeServiceAccessError
from codev_platform.core.runtime_models import require_sha256


_MAX_POSIX_ID = (1 << 32) - 2
_ZERO_SHA256 = "0" * 64


def _require_service_identity(service_uid: int, service_gid: int) -> tuple[int, int]:
    if type(service_uid) is not int or not 0 < service_uid <= _MAX_POSIX_ID:
        raise RuntimeServiceAccessError("运行时服务 UID 必须为严格正整数")
    if type(service_gid) is not int or not 0 < service_gid <= _MAX_POSIX_ID:
        raise RuntimeServiceAccessError("运行时服务 GID 必须为严格正整数")
    return service_uid, service_gid


def _require_object_ids(values: tuple[str, ...], label: str) -> tuple[str, ...]:
    if type(values) is not tuple or not values:
        raise RuntimeServiceAccessError("运行时服务对象 ID 集合无效")
    normalized: list[str] = []
    for value in values:
        try:
            object_id = require_sha256(value, field=f"{label}_id")
        except (TypeError, ValueError):
            raise RuntimeServiceAccessError("运行时服务对象 ID 无效") from None
        if object_id == _ZERO_SHA256:
            raise RuntimeServiceAccessError("运行时服务对象 ID 不能为全零")
        normalized.append(object_id)
    if len(set(normalized)) != len(normalized):
        raise RuntimeServiceAccessError("运行时服务对象 ID 不能重复")
    return tuple(sorted(normalized))


def _require_linux_root() -> None:
    if os.name != "posix" or not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise RuntimeServiceAccessError("运行时服务访问发布只支持 Linux root")


def _normalize_runtime_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise RuntimeServiceAccessError("运行时服务根必须是规范绝对路径")
    normalized = Path(os.path.abspath(os.fspath(candidate)))
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeServiceAccessError("运行时服务根不可用") from None
    if candidate != normalized or resolved != normalized or normalized == Path(normalized.anchor):
        raise RuntimeServiceAccessError("运行时服务根必须是规范非链接目录")
    return normalized


def _require_nonzero_sha256(value: object, field: str) -> str:
    try:
        digest = require_sha256(value, field=field)
    except (TypeError, ValueError):
        raise RuntimeServiceAccessError("运行时服务对象摘要无效") from None
    if digest == _ZERO_SHA256:
        raise RuntimeServiceAccessError("运行时服务对象摘要不能为全零")
    return digest
