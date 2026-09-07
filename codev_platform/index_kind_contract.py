"""生产环境必须构建的索引种类中立契约。"""

from __future__ import annotations


# 顺序同时用于执行、回执与验收摘要；新增种类必须先提供 runner 和迁移方案。
REQUIRED_INDEX_KINDS: tuple[str, ...] = (
    "chroma",
    "codegraph",
    "ingest",
    "code_vec",
)


__all__ = ["REQUIRED_INDEX_KINDS"]
