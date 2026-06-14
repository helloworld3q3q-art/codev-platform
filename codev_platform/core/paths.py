"""path conventions for multi-project AI tooling.

设计约定:
- 基目录 PLATFORM_DATA_DIR 环境变量覆盖 (默认 <repo>/data/)
  本地原型: <repo>/data/
  server 部署: /var/lib/platform/data/ (或类似)
  迁移: 整个 data/ 目录可打包带走

- chroma: **每项目独立库**, 不再单库多 collection (chromadb 1.5.9 多 collection 共库 compaction
    会损坏整库, 见 docs daily-summary-2026-06-12 §十.1 + 记忆 chromadb-multiflush-compaction):
    路径: data/chroma/docs/<project_id>/   (platform_docs, 见 chroma_docs_dir)
          data/chroma/code_vec/<project_id>/ (代码向量, 见 recall.code_vector_store)
          data/chroma/                       (根库仅余 agent-memory 单 collection)
    每库内 collection 仍带前缀名 <project_id>__<base_name>(一库一 collection, 名义保留)。

- codegraph: 每 project 独立 SQLite DB
    路径: data/codegraph/<project_id>/codegraph.db
"""
from __future__ import annotations

import os
from pathlib import Path

from codev_platform.core.project_id import validate as _validate_project_id


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


def logs_dir() -> Path:
    """运行日志目录 (data_root/logs)。

    随 data/ 可打包带走, 且避免写进 import 包目录 —— wheel / 只读安装下包目录不可写,
    日志落 data_root 才稳。目录按需创建 (幂等)。
    """
    d = data_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chroma_dir() -> Path:
    """chroma 持久化根目录。

    历史上 platform_docs 是"单 DB 多 collection"(各项目 collection 同住根库),但 chromadb 1.5.9
    的 compaction 在"库里已存在别 collection 时给另一 collection 做 upsert"会损坏整库
    (见 chromadb-multiflush-compaction)。故 platform_docs 改为每项目独立库 `docs/<pid>/`
    (见 chroma_docs_dir),与 code_vec(`code_vec/<pid>`)一致。根库现仅余 agent-memory 单 collection。"""
    return data_root() / "chroma"


def chroma_docs_dir(project_id: str) -> Path:
    """platform_docs 每项目独立 chroma 库目录 (`data/chroma/docs/<pid>/`)。

    每库只含该项目一个 collection → 永不触发 chromadb 1.5.9 多 collection compaction 损坏。
    DB / manifest / .last_build 戳都落这里; 全局 .reindex.lock 仍在 chroma_dir() 根做 GPU 串行化。"""
    project_id = _validate_project_id(project_id)  # 防路径穿越 (../outside 等)
    return chroma_dir() / "docs" / project_id


def chroma_docs_data_dir(project_id: str) -> Path:
    """platform_docs **当前可用库目录**(atomic handoff 解析后)。

    reader(daemon client)与增量 writer 经此拿"当前 build"; 无 handoff pointer
    (旧布局/首次)→ 退回 `chroma_docs_dir` 本身(100% 向后兼容)。full rebuild
    不走这(writer 经 `index_handoff.begin_build` 建 side, commit 后才被这解析到)。
    `.last_build` 戳 / `.reindex.lock` 等**控制路径仍用 `chroma_docs_dir`(base 根)**。
    见 docs/plans/roadmap-2026-06-07/phase1-atomic-handoff-plan-2026-06-14.md。"""
    from codev_platform.core.index_handoff import resolve_current
    return resolve_current(chroma_docs_dir(project_id))


def chroma_collection_name(project_id: str, base: str) -> str:
    """带 project_id 前缀的 collection 名。例 (openclaw-stock, platform_docs) -> 'openclaw-stock__platform_docs'。"""
    project_id = _validate_project_id(project_id)  # 防 collection 名污染 (任意 caller)
    return f"{project_id}{COLLECTION_SEP}{base}"


def codegraph_db_path(project_id: str) -> Path:
    """codegraph 索引集中到平台后的 per-project 路径。

    2026-05-30 起: 业务仓 `.codegraph` 做成 junction 指向这里 (`codev-platform codegraph link`),
    索引数据物理落平台 data/, 平台经 SSE 服务 + reindex 写穿 junction 也落这里。
    第三方 codegraph 工具仍以为 `.codegraph` 在仓内 (junction 透明)。
    """
    return codegraph_index_dir(project_id) / "codegraph.db"


def codegraph_index_dir(project_id: str) -> Path:
    """codegraph 索引目录 (junction target)。平台集中存放。
    例: data/codegraph_ext/<project_id>/codegraph/ (含 codegraph.db + config.json + wal)。"""
    project_id = _validate_project_id(project_id)  # 防路径穿越 (../outside 等)
    return data_root() / "codegraph_ext" / project_id / "codegraph"


def index_manifest_path() -> Path:
    """统一索引构建 manifest 库 (roadmap-2026-06-07 Phase 1)。

    多租户共享单库 (按 project_id 行隔离), 记录每个 (project_id, index_kind) 最近一次
    构建的 commit/耗时/状态 —— 把分散的新鲜度 (chroma .last_build / graph ingest_meta /
    codegraph mtime) 统一成一条可查记录。落 data_root/index_manifest.sqlite。"""
    return data_root() / "index_manifest.sqlite"
