"""代码向量构建目标及写端生命周期边界。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


def _noop_close_writer() -> None:
    """为测试替身和无外部 client 的 target 提供幂等空关闭动作。"""


@dataclass(frozen=True)
class CodeVecBuildTarget:
    """一次构建的写入目录、集合与 checkpoint 身份。"""

    build_dir: Path
    manifest_path: Path
    meta_path: Path
    collection: object
    build_id: str | None
    resumed: bool
    fingerprint: dict[str, object]
    embedding_dimension: int | None
    close_writer: Callable[[], None] = _noop_close_writer


def close_writer_before_proof(target: CodeVecBuildTarget) -> None:
    """释放本次写端，再让独立进程读取持久化 collection。"""
    try:
        target.close_writer()
    except Exception as exc:  # noqa: BLE001 - 未释放写端时不得执行跨进程成功证明
        raise RuntimeError("code_vec 写入客户端关闭失败，拒绝跨进程完整性证明") from exc


__all__ = ["CodeVecBuildTarget", "close_writer_before_proof"]
