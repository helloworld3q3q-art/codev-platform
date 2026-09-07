"""运行时服务访问的证明与内部协作模型。"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuntimeServiceAccessError(RuntimeError):
    """运行时对象无法安全发布给目标服务账号。"""


@dataclass(frozen=True, slots=True)
class RuntimeServiceNamespaceProof:
    """不携带路径的父命名空间收敛证明。"""

    service_uid: int
    service_gid: int
    directory_count: int
    mode: int


@dataclass(frozen=True, slots=True)
class RuntimeServiceContentProof:
    """不携带路径或内容的服务主组发布证明。"""

    access_profile: str
    service_uid: int
    service_gid: int
    base_ids: tuple[str, ...]
    release_ids: tuple[str, ...]
    base_metadata_sha256: tuple[str, ...]
    base_inventory_sha256: tuple[str, ...]
    release_metadata_sha256: tuple[str, ...]
    entries: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class RuntimeServiceAccessProof:
    """父链与内容对象的聚合只读证明。"""

    namespace: RuntimeServiceNamespaceProof
    content: RuntimeServiceContentProof


@dataclass(frozen=True, slots=True)
class _ContentRequest:
    root: Path
    service_uid: int
    service_gid: int
    base_ids: tuple[str, ...]
    release_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ServiceAccessPorts:
    id_lock: Callable[..., AbstractContextManager[None]]
    read_release_base_id_locked: Callable[[Path, str], str]
    verify_base_locked: Callable[[Path, str], Any]
    verify_release_locked: Callable[..., Any]
    sha256_file: Callable[[Path], str]


@dataclass(frozen=True, slots=True)
class _StaticSnapshot:
    base_models: tuple[Any, ...]
    release_models: tuple[Any, ...]
    base_metadata_sha256: tuple[str, ...]
    base_inventory_sha256: tuple[str, ...]
    release_metadata_sha256: tuple[str, ...]


@dataclass(slots=True)
class _NamespaceEntry:
    name: str
    descriptor: int
    metadata: os.stat_result


# 公开类型继续声明为原门面模块，保持 repr、类型提示与 pickle 查找路径兼容。
_PUBLIC_MODULE = "codev_platform.runtime_service_access"
for _public_type in (
    RuntimeServiceAccessError,
    RuntimeServiceNamespaceProof,
    RuntimeServiceContentProof,
    RuntimeServiceAccessProof,
):
    _public_type.__module__ = _PUBLIC_MODULE


__all__ = [
    "RuntimeServiceAccessError",
    "RuntimeServiceAccessProof",
    "RuntimeServiceContentProof",
    "RuntimeServiceNamespaceProof",
]
