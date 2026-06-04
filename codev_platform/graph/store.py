"""统一图谱 sqlite 存储 (Phase 3 起步) —— AnalyzerResult 落盘 / 读回。

定位:把各 analyzer 插件产出的统一模型 (graph/schema.py:AnalyzerResult) 持久化到
一个 **per-project 隔离** 的 sqlite,供平台核心 / Agent 查询。与 cross-link /
codegraph 各自的 sqlite **完全独立**(它们是数据来源,本 store 是聚合落点),
互不污染。

路径约定 (与 core/paths.py 风格一致,data_root 下 per-project 文件):
    data/graph_store/<project_id>.sqlite

表结构 (对应 schema.py 四类中性类型 + 一张来源元数据表):
    nodes      GraphNode  (id 全局唯一, meta_json 装不透明袋子)
    edges      GraphEdge  (source/target -> nodes.id, meta_json)
    evidences  Evidence   (按插件归属, 顺序保留)
    findings   Finding    (node_ids / evidence_ids 以 JSON 数组存)
    ingest_meta 每个 plugin 最近一次 ingest 的元数据 (计数 / 时间戳)

幂等:upsert_result 按 (project_id, plugin) 维度先清后写 —— 同一插件重灌会替换它
上次的全部产出,不与别的插件互相影响 (plugin 列做归属标记)。这样多插件 ingest
进同一 project 库不会彼此覆盖,而同一插件重跑保持幂等。

设计原则 (对齐 cross_link/schema.py):全 sqlite 零外部依赖;不吞错 (不用裸 except);
project_id 经 paths 校验防路径穿越。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from codev_platform.core.paths import data_root
from codev_platform.core.project_id import validate as _validate_project_id
from codev_platform.graph.schema import (
    AnalyzerResult,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
)


# 每表 DDL 单独成常量:SCHEMA_SQL 拼全量, _REBUILD_DDL 供旧库迁移复用同一份定义
# (避免迁移时再抄一遍表结构, 防漂移)。
# project_id 列说明:nodes 自始带 project_id (共库纵深防御);edges/evidences/findings
# 后补 (C1 列对齐) —— evidences/findings 主键纳入 project_id, 因 seq 跨 project 会撞;
# edges 主键也纳入 project_id, 与 nodes 同源隔离。
_NODES_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT NOT NULL,
    plugin      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    file        TEXT,
    line        INTEGER,
    language    TEXT,
    meta_json   TEXT,
    PRIMARY KEY (id, plugin)
);
"""

_EDGES_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS edges (
    project_id  TEXT NOT NULL,
    plugin      TEXT NOT NULL,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    confidence  REAL DEFAULT 1.0,
    meta_json   TEXT,
    PRIMARY KEY (project_id, plugin, source, target, kind)
);
"""

_EVIDENCES_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS evidences (
    project_id  TEXT NOT NULL,
    plugin      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    source      TEXT NOT NULL,
    detail      TEXT NOT NULL,
    file        TEXT,
    line        INTEGER,
    confidence  REAL DEFAULT 1.0,
    meta_json   TEXT,
    PRIMARY KEY (project_id, plugin, seq)
);
"""

_FINDINGS_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS findings (
    project_id     TEXT NOT NULL,
    plugin         TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    severity       TEXT NOT NULL,
    title          TEXT NOT NULL,
    detail         TEXT,
    node_ids_json  TEXT,
    evidence_ids_json TEXT,
    meta_json      TEXT,
    PRIMARY KEY (project_id, plugin, seq)
);
"""

_INGEST_META_DDL: Final[str] = """
CREATE TABLE IF NOT EXISTS ingest_meta (
    plugin          TEXT PRIMARY KEY,
    plugin_version  TEXT,
    node_count      INTEGER,
    edge_count      INTEGER,
    evidence_count  INTEGER,
    finding_count   INTEGER,
    ingested_at     TEXT
);
"""

_INDEX_DDL: Final[str] = """
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_plugin ON nodes(plugin);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
"""

SCHEMA_SQL: Final[str] = "\n".join(
    (_NODES_DDL, _EDGES_DDL, _EVIDENCES_DDL, _FINDINGS_DDL, _INGEST_META_DDL, _INDEX_DDL)
)

# 旧库迁移复用:表名 -> 该表当前 DDL。仅 C1 后补 project_id 的三表需要 rebuild 迁移。
_REBUILD_DDL: Final[dict[str, str]] = {
    "edges": _EDGES_DDL,
    "evidences": _EVIDENCES_DDL,
    "findings": _FINDINGS_DDL,
}


def graph_store_path(project_id: str) -> Path:
    """统一图谱 sqlite 路径, per-project 隔离。

    例: data/graph_store/openclaw-stock.sqlite
    """
    project_id = _validate_project_id(project_id)  # 防路径穿越 (../outside 等)
    return data_root() / "graph_store" / f"{project_id}.sqlite"


def _migrate_project_id_columns(conn: sqlite3.Connection, project_id: str) -> None:
    """旧库 (edges/evidences/findings 无 project_id 列) 前向迁移到 C1 列对齐 schema。

    每表:已存在且缺 project_id 列 → rename 旧表 + 建新表 (含 project_id + 新主键) +
    回填 project_id = 本文件 pid + drop 旧表。数据不丢, 无需重 reindex。
    - 新库 (表不存在):跳过, 留给 executescript(SCHEMA_SQL) 直接建新 schema。
    - 已迁移 (project_id 列已在):跳过 (幂等)。

    表名取自 _REBUILD_DDL 的固定键 (edges/evidences/findings), 非外部输入, 无注入风险。
    """
    for table, ddl in _REBUILD_DDL.items():
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if not exists:
            continue
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "project_id" in cols:
            continue
        # 旧 schema → 重建。旧列原样搬, project_id 用本文件 pid 回填 (per-project 库
        # 内所有行同属一个 project)。
        old_cols = ", ".join(cols)
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}__c1old")
        conn.executescript(ddl)
        conn.execute(
            f"INSERT INTO {table} (project_id, {old_cols}) "
            f"SELECT ?, {old_cols} FROM {table}__c1old",
            (project_id,),
        )
        conn.execute(f"DROP TABLE {table}__c1old")
    conn.commit()


def open_store(project_id: str, *, path: Path | None = None) -> sqlite3.Connection:
    """打开 (或新建) 某 project 的统一图谱 sqlite, 返回已初始化 schema 的连接。

    Args:
        project_id: 项目隔离键 (决定默认文件名)。
        path:       显式覆盖 DB 路径 (测试用);None 走 graph_store_path。
    """
    pid = _validate_project_id(project_id)
    p = path if path is not None else graph_store_path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate_project_id_columns(conn, pid)  # 旧库前向迁移 (须在 executescript 前)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def _dump_meta(meta: dict) -> str | None:
    return json.dumps(meta, ensure_ascii=False) if meta else None


def _load_meta(meta_json: str | None) -> dict:
    if not meta_json:
        return {}
    return json.loads(meta_json)


def upsert_result(
    conn: sqlite3.Connection,
    project_id: str,
    result: AnalyzerResult,
) -> None:
    """把一次插件分析产出写入 store, 按 plugin 归属幂等替换。

    幂等策略:先删该 plugin 上次写入的全部行 (nodes/edges/evidences/findings/meta),
    再整体重写。同一插件重跑 = 全替换;不同插件互不影响 (plugin 列隔离)。

    plugin 名取 result.plugin;为空则用 "(unknown)" 兜底 (executor 通常已补全)。
    """
    project_id = _validate_project_id(project_id)
    plugin = result.plugin or "(unknown)"
    version = result.plugin_version or ""

    # 先清该 plugin 旧数据 (幂等替换)。nodes/edges/evidences/findings 都有 project_id 列
    # (C1 列对齐) → DELETE 按 (plugin, project_id) 双键, 共享库下不误删别项目同插件行。
    # ingest_meta 无 project_id 列 (per-plugin 计数, per-project 库即隔离边界), 仅按 plugin。
    conn.execute(
        "DELETE FROM nodes WHERE plugin = ? AND project_id = ?", (plugin, project_id)
    )
    for table in ("edges", "evidences", "findings"):
        conn.execute(
            f"DELETE FROM {table} WHERE plugin = ? AND project_id = ?",
            (plugin, project_id),
        )
    conn.execute("DELETE FROM ingest_meta WHERE plugin = ?", (plugin,))

    conn.executemany(
        """INSERT INTO nodes
             (id, plugin, kind, name, project_id, file, line, language, meta_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                # project_id 强制用校验过的入参 (函数已 _validate), 不用 n.project_id,
                # 防写时把别项目 id 写进本项目库。
                n.id, plugin, n.kind, n.name, project_id,
                n.file, n.line, n.language, _dump_meta(n.meta),
            )
            for n in result.nodes
        ],
    )
    conn.executemany(
        """INSERT OR REPLACE INTO edges
             (project_id, plugin, source, target, kind, confidence, meta_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (project_id, plugin, e.source, e.target, e.kind, e.confidence,
             _dump_meta(e.meta))
            for e in result.edges
        ],
    )
    conn.executemany(
        """INSERT INTO evidences
             (project_id, plugin, seq, source, detail, file, line, confidence, meta_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                project_id, plugin, i, ev.source, ev.detail, ev.file, ev.line,
                ev.confidence, _dump_meta(ev.meta),
            )
            for i, ev in enumerate(result.evidences)
        ],
    )
    conn.executemany(
        """INSERT INTO findings
             (project_id, plugin, seq, kind, severity, title, detail,
              node_ids_json, evidence_ids_json, meta_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                project_id, plugin, i, f.kind, f.severity, f.title, f.detail,
                json.dumps(f.node_ids, ensure_ascii=False),
                json.dumps(f.evidence_ids, ensure_ascii=False),
                _dump_meta(f.meta),
            )
            for i, f in enumerate(result.findings)
        ],
    )
    conn.execute(
        """INSERT OR REPLACE INTO ingest_meta
             (plugin, plugin_version, node_count, edge_count,
              evidence_count, finding_count, ingested_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            plugin, version,
            len(result.nodes), len(result.edges),
            len(result.evidences), len(result.findings),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def load_graph(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    plugin: str | None = None,
) -> AnalyzerResult:
    """读回 store 内某 project 的图谱, 重组成 AnalyzerResult。

    plugin=None 读全部插件的合集 (plugin 字段被丢弃, 因为 AnalyzerResult 是
    单产出模型);plugin=<name> 只读该插件的产出 (含 plugin/version 归属)。
    """
    project_id = _validate_project_id(project_id)
    # 四表 (nodes/edges/evidences/findings) 都按 project_id 过滤 (C1 列对齐, 共享库防串);
    # 给 plugin 则再 AND plugin。四表过滤条件一致, 共用 where/params。
    where = "WHERE project_id = ?"
    params: tuple = (project_id,)
    if plugin:
        where += " AND plugin = ?"
        params = (project_id, plugin)

    node_where = where
    node_params = params

    nodes = [
        GraphNode(
            id=row[0], kind=row[1], name=row[2], project_id=row[3],
            file=row[4], line=row[5], language=row[6], meta=_load_meta(row[7]),
        )
        for row in conn.execute(
            f"SELECT id, kind, name, project_id, file, line, language, meta_json "
            f"FROM nodes {node_where} ORDER BY id",
            node_params,
        )
    ]
    edges = [
        GraphEdge(
            source=row[0], target=row[1], kind=row[2],
            confidence=row[3], meta=_load_meta(row[4]),
        )
        for row in conn.execute(
            f"SELECT source, target, kind, confidence, meta_json "
            f"FROM edges {where} ORDER BY source, target, kind",
            params,
        )
    ]
    evidences = [
        Evidence(
            source=row[0], detail=row[1], file=row[2], line=row[3],
            confidence=row[4], meta=_load_meta(row[5]),
        )
        for row in conn.execute(
            f"SELECT source, detail, file, line, confidence, meta_json "
            f"FROM evidences {where} ORDER BY plugin, seq",
            params,
        )
    ]
    findings = [
        Finding(
            kind=row[0], severity=row[1], title=row[2], detail=row[3],
            node_ids=json.loads(row[4]) if row[4] else [],
            evidence_ids=json.loads(row[5]) if row[5] else [],
            meta=_load_meta(row[6]),
        )
        for row in conn.execute(
            f"SELECT kind, severity, title, detail, node_ids_json, "
            f"evidence_ids_json, meta_json FROM findings {where} ORDER BY plugin, seq",
            params,
        )
    ]
    out = AnalyzerResult(
        nodes=nodes, edges=edges, evidences=evidences, findings=findings,
    )
    if plugin:
        out.plugin = plugin
        row = conn.execute(
            "SELECT plugin_version FROM ingest_meta WHERE plugin = ?", (plugin,)
        ).fetchone()
        if row:
            out.plugin_version = row[0]
    return out


def stats(conn: sqlite3.Connection) -> dict:
    """聚合统计:总计 + 每插件 ingest 元数据 (供 CLI / Agent 概览)。"""
    counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("nodes", "edges", "evidences", "findings")
    }
    plugins = [
        {
            "plugin": row[0],
            "plugin_version": row[1],
            "node_count": row[2],
            "edge_count": row[3],
            "evidence_count": row[4],
            "finding_count": row[5],
            "ingested_at": row[6],
        }
        for row in conn.execute(
            "SELECT plugin, plugin_version, node_count, edge_count, "
            "evidence_count, finding_count, ingested_at FROM ingest_meta "
            "ORDER BY plugin"
        )
    ]
    return {"totals": counts, "plugins": plugins}
