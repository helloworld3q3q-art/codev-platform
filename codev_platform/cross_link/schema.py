"""Cross-layer KG sqlite schema + 初始化工具。

DB 位置：<repo_root>/data/codegraph_ext/<project_id>/cross_layer.sqlite
legacy (一次性 fallback)：<repo_root>/data/codegraph_ext/cross_layer.sqlite

表结构：
- nodes: 节点（table / column / java_method / flyway_migration / 后续 python_method / frontend_api）
- edges: 关系（defines_table / defines_column / queries_table / writes_table / updates_table / 后续 calls_api ...）
- build_meta: 索引构建元数据（last_build_at / 各 kind 计数）

设计原则（与 codegraph.db 独立，不污染第三方数据）：
- 节点 (kind, name, path) 唯一约束 — 多次扫描幂等
- 边附 confidence + evidence — 区分精确锚点 (1.0) 与降级 fuzzy (0.7)
- 全 sqlite，零依赖外部服务
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Final

# codev-platform 包内 import (pip install -e codev-platform 后)
from codev_platform.core.project_id import ProjectIdError, resolve_local
from codev_platform.core.paths import (
    cross_link_db_path,
    cross_link_legacy_db_path,
)

# 多项目隔离: data/codegraph_ext/<project_id>/cross_layer.sqlite
# legacy: data/codegraph_ext/cross_layer.sqlite (无 pid 子目录, 一次性 fallback)
try:
    PROJECT_ID: Final[str] = resolve_local()
except ProjectIdError as _pid_exc:
    print(f"[codev_platform.cross_link.schema] FATAL: {_pid_exc!s}", file=sys.stderr, flush=True)
    sys.exit(1)

DB_PATH: Final[Path] = cross_link_db_path(PROJECT_ID)
DB_DIR: Final[Path] = DB_PATH.parent
LEGACY_DB_PATH: Final[Path] = cross_link_legacy_db_path()



SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS nodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    parent_id   INTEGER,
    path        TEXT,
    line        INTEGER,
    language    TEXT,
    meta_json   TEXT,
    UNIQUE(kind, name, path)
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id      INTEGER NOT NULL,
    rel         TEXT NOT NULL,
    dst_id      INTEGER NOT NULL,
    confidence  REAL DEFAULT 1.0,
    evidence    TEXT,
    FOREIGN KEY (src_id) REFERENCES nodes(id),
    FOREIGN KEY (dst_id) REFERENCES nodes(id),
    UNIQUE(src_id, rel, dst_id)
);

CREATE TABLE IF NOT EXISTS build_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_kind_name ON nodes(kind, name);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_id);
CREATE INDEX IF NOT EXISTS idx_edges_rel ON edges(rel);
"""


def open_db(path: Path | None = None, *, fresh: bool = False) -> sqlite3.Connection:
    """打开（或重建）cross-layer sqlite 数据库。

    Args:
        path: DB 路径，None 用默认 data/codegraph_ext/<project_id>/cross_layer.sqlite
        fresh: True 时删除旧 DB 重建（用于 build_index --force）
    Returns:
        sqlite3.Connection (已开启 foreign_keys + WAL)

    backward compat: path=None 且新路径不存在但 legacy (data/codegraph_ext/cross_layer.sqlite)
    存在时 -> 读 legacy + 一次性警告。fresh=True 不走 fallback (永远写新路径)。
    """
    if path is not None:
        p = path
    elif fresh:
        p = DB_PATH
    elif DB_PATH.exists():
        p = DB_PATH
    elif LEGACY_DB_PATH.exists():
        p = LEGACY_DB_PATH
        print(
            f"[cross_link] WARN: 使用 legacy DB {LEGACY_DB_PATH}, "
            f"重跑 build_index.py 后会迁移到 {DB_PATH}。",
            file=sys.stderr,
            flush=True,
        )
    else:
        p = DB_PATH  # 不存在就由后续 mkdir + executescript 建
    p.parent.mkdir(parents=True, exist_ok=True)
    in_place_reset = False
    if fresh and p.exists():
        try:
            p.unlink()
        except PermissionError:
            # DB 被外部进程 (如 Java codegraph-api) 占用 → 降级 in-place 清表
            in_place_reset = True
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    if in_place_reset:
        # 关闭外键约束后 DROP 所有表,然后重建 schema (不需要 unlink 文件)
        conn.execute("PRAGMA foreign_keys = OFF")
        rows = list(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        for (name,) in rows:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
        conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def upsert_node(
    conn: sqlite3.Connection,
    kind: str,
    name: str,
    *,
    path: str | None = None,
    line: int | None = None,
    language: str | None = None,
    parent_id: int | None = None,
    meta_json: str | None = None,
) -> int:
    """UPSERT node 按 (kind, name, path) 唯一约束。返回 node id。"""
    cur = conn.execute(
        """INSERT INTO nodes (kind, name, parent_id, path, line, language, meta_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(kind, name, path) DO UPDATE SET
             line       = COALESCE(excluded.line, nodes.line),
             language   = COALESCE(excluded.language, nodes.language),
             parent_id  = COALESCE(excluded.parent_id, nodes.parent_id),
             meta_json  = COALESCE(excluded.meta_json, nodes.meta_json)
           RETURNING id""",
        (kind, name, parent_id, path, line, language, meta_json),
    )
    row = cur.fetchone()
    return int(row[0])


def upsert_edge(
    conn: sqlite3.Connection,
    src_id: int,
    rel: str,
    dst_id: int,
    *,
    confidence: float = 1.0,
    evidence: str | None = None,
) -> None:
    """UPSERT edge 按 (src_id, rel, dst_id) 唯一约束。"""
    conn.execute(
        """INSERT INTO edges (src_id, rel, dst_id, confidence, evidence)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(src_id, rel, dst_id) DO UPDATE SET
             confidence = MAX(edges.confidence, excluded.confidence),
             evidence   = COALESCE(excluded.evidence, edges.evidence)""",
        (src_id, rel, dst_id, confidence, evidence),
    )


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """写入 build_meta（如 last_build_at / flyway_count）。"""
    conn.execute(
        """INSERT INTO build_meta (key, value) VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.execute("SELECT value FROM build_meta WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else None
