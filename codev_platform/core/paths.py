"""path conventions for multi-project AI tooling.

设计约定:
- 基目录 PLATFORM_DATA_DIR 环境变量覆盖 (默认 <repo>/data/)
  本地原型: <repo>/data/
  server 部署: /var/lib/platform/data/ (或类似)
  迁移: 整个 data/ 目录可打包带走

- chroma: 单 PersistentClient 多 collection, 通过 collection 名前缀隔离
    路径: data/chroma/  (扁平, 不按 project 分目录)
    collection 命名: <project_id>__<base_name>
        例: openclaw-stock__platform_docs
            openclaw-stock__platform_docs_bm25_meta

- codegraph: 每 project 独立 SQLite DB
    路径: data/codegraph/<project_id>/codegraph.db

- cross_link: 每 project 独立 SQLite DB
    路径: data/cross_link/<project_id>/index.db
"""
from __future__ import annotations

import os
from pathlib import Path


_DATA_DIR_ENV = "PLATFORM_DATA_DIR"
COLLECTION_SEP = "__"  # chroma collection 前缀分隔符


def _business_repo_root() -> Path:
    """业务项目仓根 = 从 cwd 向上找到含 .claude/project.json 的目录。

    codev-platform 作为 pip 包安装后, 不能用 __file__ 推导仓根 (那是 package
    自身的位置)。必须以业务项目 cwd 为锚点, 与 project_id resolver 同源。
    """
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".claude" / "project.json").is_file():
            return parent
    return cwd  # fallback: 无 .claude/project.json 时退回 cwd (resolver 会硬失败 + 提示)


def data_root() -> Path:
    """基目录, 可由 PLATFORM_DATA_DIR 覆盖, 否则 <business-repo>/data/。"""
    env = os.environ.get(_DATA_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return _business_repo_root() / "data"


def chroma_dir() -> Path:
    """chroma 持久化目录 (单 DB 多 collection)。"""
    return data_root() / "chroma"


def chroma_collection_name(project_id: str, base: str) -> str:
    """带 project_id 前缀的 collection 名。例 (openclaw-stock, platform_docs) -> 'openclaw-stock__platform_docs'。"""
    return f"{project_id}{COLLECTION_SEP}{base}"


def codegraph_db_path(project_id: str) -> Path:
    """第三方 codegraph MCP server 走 .codegraph/codegraph.db, 天然 per-repo。
    本函数保留, 供未来 server 化时改为统一目录。"""
    return data_root() / "codegraph" / project_id / "codegraph.db"


def cross_link_db_path(project_id: str) -> Path:
    """cross-link KG sqlite, per-project 隔离。
    legacy 路径: data/codegraph_ext/cross_layer.sqlite (无 project_id 子目录)
    新路径:      data/codegraph_ext/<project_id>/cross_layer.sqlite
    """
    return data_root() / "codegraph_ext" / project_id / "cross_layer.sqlite"


def cross_link_legacy_db_path() -> Path:
    """legacy cross-link DB, 用于一次性 fallback 兼容 (重建后即弃用)。"""
    return data_root() / "codegraph_ext" / "cross_layer.sqlite"
