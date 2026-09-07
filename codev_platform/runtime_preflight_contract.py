"""运行时预检的稳定契约与全局时限。"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PROBE_TIMEOUT_SEC = 30.0
CHECK_KINDS = {
    "runtime_identity_release": "runtime_identity",
    "runtime_root_traverse": "directory_traverse",
    "runtime_release_read_execute": "directory_rx",
    "runtime_python_read_execute": "file_rx",
    "runtime_base_read_execute": "directory_rx",
    "runtime_base_python_read_execute": "file_rx",
    "application_import_source": "import_source",
    "data_root_read_execute": "directory_rx",
    "logs_directory_rw": "directory_rw",
    "chroma_directory_rw": "directory_rw",
    "codegraph_directory_rw": "directory_rw",
    "graph_store_directory_rw": "directory_rw",
    "agent_memory_vector_directory_rw": "directory_rw",
    "file_queue_operational_rw": "file_queue_operational",
    "queue_backend_readonly": "queue_readonly",
    "journal_atomic_replace": "atomic_directory",
    "run_lock_create_flock": "flock_directory",
}
FIXED_ERROR_NAMES = frozenset({*CHECK_KINDS, "preflight_setup"})


class RuntimePreflightError(RuntimeError):
    """固定检查未通过；异常正文不携带路径或底层错误。"""

    def __init__(self, check_name: str) -> None:
        if check_name not in FIXED_ERROR_NAMES:
            raise ValueError("权限探针检查名不受支持")
        self.check_name = check_name
        super().__init__(f"权限探针失败：{check_name}")


@dataclass(frozen=True, slots=True)
class PathRequirement:
    """单个固定探针；敏感路径和数据库地址均不进入 repr。"""

    name: str
    kind: str
    path: Path | None = field(repr=False)
    forbidden_root: Path | None = field(default=None, repr=False)
    database_dsn: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        expected_kind = CHECK_KINDS.get(self.name)
        if expected_kind is None or self.kind != expected_kind:
            raise ValueError("权限探针名称与类型不匹配")
        pathless = self.kind == "queue_readonly"
        if pathless != (self.path is None):
            raise ValueError("权限探针路径形状不匹配")
        if self.path is not None and not isinstance(self.path, Path):
            raise TypeError("权限探针路径必须是 Path")
        if self.forbidden_root is not None and not isinstance(self.forbidden_root, Path):
            raise TypeError("禁止来源根必须是 Path")
        if self.database_dsn is not None:
            if not pathless or type(self.database_dsn) is not str or not self.database_dsn.strip():
                raise ValueError("数据库探针上下文无效")


@dataclass(frozen=True, slots=True)
class ProbeDeadline:
    """一次完整预检共享的单调时钟截止点。"""

    expires_at: float
    monotonic: Callable[[], float] = field(repr=False)

    @classmethod
    def after(
        cls,
        timeout_sec: float,
        monotonic: Callable[[], float],
    ) -> ProbeDeadline:
        if (
            isinstance(timeout_sec, bool)
            or not isinstance(timeout_sec, (int, float))
            or not math.isfinite(timeout_sec)
            or timeout_sec <= 0
            or not callable(monotonic)
        ):
            raise ValueError("权限探针整体时限必须是正有限数")
        return cls(monotonic() + float(timeout_sec), monotonic)

    def remaining_seconds(self) -> float:
        remaining = self.expires_at - self.monotonic()
        if remaining <= 0:
            raise TimeoutError("权限探针超过整体时限")
        return remaining

    def ensure(self) -> None:
        self.remaining_seconds()

    def bounded_seconds(self, maximum: int) -> int:
        """生成 libpq 所需的正整数秒时限，且不超过整体剩余时间。"""
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("秒级时限上限无效")
        return max(1, min(maximum, math.ceil(self.remaining_seconds())))

    def bounded_milliseconds(self, maximum: int) -> int:
        """生成 PostgreSQL 会话设置使用的正整数毫秒时限。"""
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("毫秒级时限上限无效")
        remaining = max(1, math.floor(self.remaining_seconds() * 1000))
        return min(maximum, remaining)


__all__ = [
    "CHECK_KINDS",
    "DEFAULT_PROBE_TIMEOUT_SEC",
    "PathRequirement",
    "ProbeDeadline",
    "RuntimePreflightError",
]
