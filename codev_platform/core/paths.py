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


def business_repo_root() -> Path:
    """业务项目仓根的公开 API (见 _business_repo_root)。

    外部 (cli / 工具脚本 / shim) 应调本函数, 不要直接引私有 `_business_repo_root`。
    """
    return _business_repo_root()


def data_root() -> Path:
    """基目录解析优先级:
    1. env PLATFORM_DATA_DIR (launcher .cmd / 显式覆盖)
    2. ~/.codev-platform/config.json data.platform_data_dir (用户级全局默认)
    3. <business-repo>/data/ (cwd 向上找 .claude/project.json 推导)
    """
    env = os.environ.get(_DATA_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    # 走 config 文件 (避免每次都设 env)
    try:
        from codev_platform.core.config import load_config, get
        cfg_val = get(load_config(), "data.platform_data_dir")
        if cfg_val:
            return Path(cfg_val).expanduser().resolve()
    except Exception:
        pass
    return _business_repo_root() / "data"


def chroma_dir() -> Path:
    """chroma 持久化目录 (单 DB 多 collection)。"""
    return data_root() / "chroma"


def chroma_collection_name(project_id: str, base: str) -> str:
    """带 project_id 前缀的 collection 名。例 (openclaw-stock, platform_docs) -> 'openclaw-stock__platform_docs'。"""
    return f"{project_id}{COLLECTION_SEP}{base}"


def codegraph_db_path(project_id: str) -> Path:
    """codegraph 索引集中到平台后的 per-project 路径 (与 cross_layer.sqlite 并排)。

    2026-05-30 起: 业务仓 `.codegraph` 做成 junction 指向这里 (`codev-platform codegraph link`),
    索引数据物理落平台 data/, 平台经 SSE 服务 + reindex 写穿 junction 也落这里。
    第三方 codegraph 工具仍以为 `.codegraph` 在仓内 (junction 透明)。
    """
    return codegraph_index_dir(project_id) / "codegraph.db"


def codegraph_index_dir(project_id: str) -> Path:
    """codegraph 索引目录 (junction target)。与 cross_link_db_path 同父, 平台集中存放。
    例: data/codegraph_ext/<project_id>/codegraph/ (含 codegraph.db + config.json + wal)。"""
    return data_root() / "codegraph_ext" / project_id / "codegraph"


def cross_link_db_path(project_id: str) -> Path:
    """cross-link KG sqlite, per-project 隔离。
    legacy 路径: data/codegraph_ext/cross_layer.sqlite (无 project_id 子目录)
    新路径:      data/codegraph_ext/<project_id>/cross_layer.sqlite
    """
    return data_root() / "codegraph_ext" / project_id / "cross_layer.sqlite"


def cross_link_legacy_db_path() -> Path:
    """legacy cross-link DB, 用于一次性 fallback 兼容 (重建后即弃用)。"""
    return data_root() / "codegraph_ext" / "cross_layer.sqlite"
