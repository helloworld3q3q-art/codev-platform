"""统一图谱 store —— GraphStore 抽象 + sqlite 实现 + 工厂。

定位:把各 analyzer 插件产出的统一模型 (graph/schema.py:AnalyzerResult) 持久化,供平台核心 /
Agent 查询。与 cross-link / codegraph 各自的存储**完全独立**(它们是数据来源,本 store 是聚合落点)。

**GraphStore Protocol** 把"图谱存哪、怎么读写、怎么审计"抽象掉:所有消费方(impact / recall /
ingest / audit / web / agent)只依赖 Protocol,后端从单机 sqlite 换多机共享 PG **不改消费方**。
选后端只在 `open_store` 工厂一处(后续 Stage B 接 PG)。**绝不暴露底层 conn / execute**——漏出
即把 sqlite 语义焊死进消费方,PG 后端接不住 = recouple(头号技术债)。审计这类"load_graph 按 pid
过滤后看不见的底层真实"(串台行 / 软产物 plugin 漂移)经 `audit_scan` 专用方法回传结构化事实。

sqlite 路径约定 (per-project 隔离):data/graph_store/<project_id>.sqlite

表结构 (对应 schema.py 四类中性类型 + 一张来源元数据表):
    nodes / edges / evidences / findings + ingest_meta (每 plugin 最近一次 ingest 计数/时间)

幂等:upsert_result 按 (project_id, plugin) 维度先清后写 —— 同插件重灌替换其上次全部产出,
多插件 ingest 进同一 project 不互相覆盖。row→中性类型的映射抽成共享 `_row_to_*` 纯函数,
sqlite / PG 两后端共用一份(防双拷漂移)。

设计原则:零外部依赖(sqlite stdlib);不吞错(不用裸 except);project_id 经 paths 校验防穿越。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from codev_platform.core.paths import data_root
from codev_platform.core.project_id import validate as _validate_project_id
from codev_platform.graph.schema import (
    SOFT_EDGE_KINDS,
    SOFT_NODE_KINDS,
    AnalyzerResult,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
)


class GraphStoreUnreadable(Exception):
    """store 无法读取(旧 schema 缺列 / 坏库 / 只读连接读不动)。后端中性 —— 审计门禁据此记
    unreadable 而非崩, 调用方无需 import sqlite3/psycopg 各后端异常类型。"""


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


def graph_store_path(project_id: str, *, root: Path | None = None) -> Path:
    """统一图谱 sqlite 路径, per-project 隔离。例: data/graph_store/openclaw-stock.sqlite

    纯路径函数(后端无关, path-only 消费方如 eval / deep-link 用); 不属 Protocol。
    """
    project_id = _validate_project_id(project_id)  # 防路径穿越 (../outside 等)
    selected_root = data_root() if root is None else Path(root)
    if not selected_root.is_absolute():
        raise ValueError("统一图谱数据根必须是绝对路径")
    return selected_root / "graph_store" / f"{project_id}.sqlite"


# ---- 中性类型 <-> 存储行 的共享纯映射(sqlite / PG 共用一份, 防双拷漂移)----

def _dump_meta(meta: dict) -> str | None:
    return json.dumps(meta, ensure_ascii=False) if meta else None


def _load_meta(meta_json: str | None) -> dict:
    if not meta_json:
        return {}
    return json.loads(meta_json)


# SELECT 列序与下列 _row_to_* 严格对应; 两后端的查询都按此列序取, 映射共用。
_NODE_COLS = "id, kind, name, project_id, file, line, language, meta_json"
_EDGE_COLS = "source, target, kind, confidence, meta_json"
_EVIDENCE_COLS = "source, detail, file, line, confidence, meta_json"
_FINDING_COLS = "kind, severity, title, detail, node_ids_json, evidence_ids_json, meta_json"


def _row_to_node(row) -> GraphNode:
    return GraphNode(
        id=row[0], kind=row[1], name=row[2], project_id=row[3],
        file=row[4], line=row[5], language=row[6], meta=_load_meta(row[7]),
    )


def _row_to_edge(row) -> GraphEdge:
    return GraphEdge(
        source=row[0], target=row[1], kind=row[2],
        confidence=row[3], meta=_load_meta(row[4]),
    )


def _row_to_evidence(row) -> Evidence:
    return Evidence(
        source=row[0], detail=row[1], file=row[2], line=row[3],
        confidence=row[4], meta=_load_meta(row[5]),
    )


def _row_to_finding(row) -> Finding:
    return Finding(
        kind=row[0], severity=row[1], title=row[2], detail=row[3],
        node_ids=json.loads(row[4]) if row[4] else [],
        evidence_ids=json.loads(row[5]) if row[5] else [],
        meta=_load_meta(row[6]),
    )


# ---- GraphStore 抽象 ----

@runtime_checkable
class GraphStore(Protocol):
    """统一图谱存储抽象。消费方只依赖本协议, 不碰底层连接。

    绝不暴露 execute()/cursor()/connection() —— 那会把后端语义漏给消费方, 换后端即失效。
    支持上下文管理器(with open_store(pid) as store: ...), ingest 的"开库后多 pass"生命周期更干净。
    """

    def load_graph(self, project_id: str, *, plugin: str | None = None) -> AnalyzerResult:
        """读回某 project 图谱(impact 全部 find_* / soft_quality / recall graph lane 唯一读路径)。"""
        ...

    def upsert_result(self, project_id: str, result: AnalyzerResult) -> None:
        """按 plugin 幂等替换写入(ingest 各 pass 唯一写路径)。"""
        ...

    def stats(self, project_id: str) -> dict:
        """某 project 的总计 + 每 plugin ingest 元数据(CLI / health / 概览)。"""
        ...

    def audit_scan(self, project_id: str) -> dict:
        """后端探查 load_graph 按 pid 过滤后看不见的真实底层: 串台行 + 软产物 plugin 漂移。

        返回纯后端事实 {foreign_project_ids:{nodes,edges}, soft_plugins:{nodes,edges}};
        canonical plugin 比对等域判定留给 audit.py(不让 store 依赖 ingest)。
        """
        ...

    def list_project_ids(self) -> list[str]:
        """枚举本 store 里出现过的 project_id(sqlite per-file 通常 [pid] + 串台残留; pg 共享库 = 全部)。"""
        ...

    def close(self) -> None:
        """释放底层资源(sqlite 关 conn / PG 归还池)。"""
        ...

    def __enter__(self) -> GraphStore:
        ...

    def __exit__(self, *exc) -> None:
        ...


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


class SqliteGraphStore:
    """GraphStore 的 sqlite 实现(单机默认)。per-project 一个 sqlite 文件。

    mode='rw'(默认): 建目录 + WAL + 旧库前向迁移 + 建 schema(写侧 ingest / 读写通用)。
    mode='ro': 纯只读连接(file:?mode=ro), **不建目录 / 不迁移 / 不建表 / 不设 WAL** —— audit
    门禁等只读场景, 不顺手改本地 sqlite。旧 schema 读不动会在查询时抛 sqlite3.Error(调用方兜底)。
    """

    def __init__(self, project_id: str, *, mode: str = "rw", path: Path | None = None) -> None:
        pid = _validate_project_id(project_id)
        self._pid = pid
        p = path if path is not None else graph_store_path(pid)
        if mode == "ro":
            # file URI + mode=ro = 纯只读(不创建/不写)。as_uri 处理路径转义(空格/反斜杠)。
            # 缺文件 / 旧 schema(edges 缺 project_id 列)/ 坏库 → 抛中性 GraphStoreUnreadable,
            # 调用方(audit / web / recall)据此优雅处理(记 unreadable / skip), 不必 import sqlite3,
            # 也不会因只读读不动而崩。迁移留写侧 ingest(只读绝不改本地存储)。
            try:
                self._conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
                self._conn.execute("SELECT project_id FROM edges LIMIT 0")
            except sqlite3.Error as exc:
                c = getattr(self, "_conn", None)
                if c is not None:
                    c.close()
                raise GraphStoreUnreadable(str(exc)) from exc
        elif mode == "rw":
            p.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(p)
            self._conn.execute("PRAGMA journal_mode = WAL")
            _migrate_project_id_columns(self._conn, pid)  # 旧库前向迁移 (须在 executescript 前)
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()
        else:
            raise ValueError(f"未知 open mode: {mode!r} (rw|ro)")

    # ---- 读 ----

    def load_graph(self, project_id: str, *, plugin: str | None = None) -> AnalyzerResult:
        try:
            return self._load_graph(project_id, plugin=plugin)
        except sqlite3.Error as exc:
            # 读路径后端中性: 锁 / 坏库 / 旧 schema 中途读失败 → 抛中性 GraphStoreUnreadable,
            # 消费方(web / recall)优雅降级不必 import sqlite3, 不向上冒 500。
            raise GraphStoreUnreadable(str(exc)) from exc

    def _load_graph(self, project_id: str, *, plugin: str | None = None) -> AnalyzerResult:
        project_id = _validate_project_id(project_id)
        conn = self._conn
        # 四表都按 project_id 过滤 (C1 列对齐, 共享库防串); 给 plugin 则再 AND plugin。
        where = "WHERE project_id = ?"
        params: tuple = (project_id,)
        if plugin:
            where += " AND plugin = ?"
            params = (project_id, plugin)

        nodes = [
            _row_to_node(row)
            for row in conn.execute(
                f"SELECT {_NODE_COLS} FROM nodes {where} ORDER BY id", params
            )
        ]
        edges = [
            _row_to_edge(row)
            for row in conn.execute(
                f"SELECT {_EDGE_COLS} FROM edges {where} ORDER BY source, target, kind",
                params,
            )
        ]
        evidences = [
            _row_to_evidence(row)
            for row in conn.execute(
                f"SELECT {_EVIDENCE_COLS} FROM evidences {where} ORDER BY plugin, seq",
                params,
            )
        ]
        findings = [
            _row_to_finding(row)
            for row in conn.execute(
                f"SELECT {_FINDING_COLS} FROM findings {where} ORDER BY plugin, seq",
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

    # ---- 写 ----

    def upsert_result(self, project_id: str, result: AnalyzerResult) -> None:
        """把一次插件分析产出写入 store, 按 plugin 归属幂等替换。

        幂等策略:先删该 plugin 上次写入的全部行 (nodes/edges/evidences/findings/meta),
        再整体重写。同一插件重跑 = 全替换;不同插件互不影响 (plugin 列隔离)。
        """
        project_id = _validate_project_id(project_id)
        conn = self._conn
        plugin = result.plugin or "(unknown)"
        version = result.plugin_version or ""

        # 先清该 plugin 旧数据 (幂等替换)。四表都有 project_id 列 → DELETE 按 (plugin, project_id)
        # 双键, 共享库下不误删别项目同插件行。ingest_meta 无 project_id 列, 仅按 plugin。
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
            # INSERT OR REPLACE: 同批重复 (id,plugin) 后者赢 —— 对齐 edges 及 PG 后端 ON CONFLICT
            # DO UPDATE(契约 parity: 同批 dup id 两后端都 last-wins, 不一个静默一个硬崩)。
            """INSERT OR REPLACE INTO nodes
                 (id, plugin, kind, name, project_id, file, line, language, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # project_id 强制用校验过的入参, 不用 n.project_id, 防写时把别项目 id 写进本项目库。
                (n.id, plugin, n.kind, n.name, project_id,
                 n.file, n.line, n.language, _dump_meta(n.meta))
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
                (project_id, plugin, i, ev.source, ev.detail, ev.file, ev.line,
                 ev.confidence, _dump_meta(ev.meta))
                for i, ev in enumerate(result.evidences)
            ],
        )
        conn.executemany(
            """INSERT INTO findings
                 (project_id, plugin, seq, kind, severity, title, detail,
                  node_ids_json, evidence_ids_json, meta_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (project_id, plugin, i, f.kind, f.severity, f.title, f.detail,
                 json.dumps(f.node_ids, ensure_ascii=False),
                 json.dumps(f.evidence_ids, ensure_ascii=False),
                 _dump_meta(f.meta))
                for i, f in enumerate(result.findings)
            ],
        )
        conn.execute(
            """INSERT OR REPLACE INTO ingest_meta
                 (plugin, plugin_version, node_count, edge_count,
                  evidence_count, finding_count, ingested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (plugin, version,
             len(result.nodes), len(result.edges),
             len(result.evidences), len(result.findings),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    # ---- 概览 / 审计 ----

    def stats(self, project_id: str) -> dict:
        """某 project 总计 + 每插件 ingest 元数据。

        count 按 project_id 过滤(共享库纵深: 旧版 stats(conn) 是全库 COUNT(*) 无 pid 过滤,
        sqlite per-file 下恰好=本项目所以无感, 但共享库会串台 —— 收口时一并修正按 pid 过滤)。
        ingest_meta 无 project_id 列(per-file 即 per-project 边界), 全表返回。
        """
        project_id = _validate_project_id(project_id)
        conn = self._conn
        counts = {
            table: int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)
            ).fetchone()[0])
            for table in ("nodes", "edges", "evidences", "findings")
        }
        plugins = [
            {
                "plugin": row[0], "plugin_version": row[1],
                "node_count": row[2], "edge_count": row[3],
                "evidence_count": row[4], "finding_count": row[5],
                "ingested_at": row[6],
            }
            for row in conn.execute(
                "SELECT plugin, plugin_version, node_count, edge_count, "
                "evidence_count, finding_count, ingested_at FROM ingest_meta "
                "ORDER BY plugin"
            )
        ]
        return {"totals": counts, "plugins": plugins}

    def audit_scan(self, project_id: str) -> dict:
        """后端探查(见 GraphStore.audit_scan)。纯后端事实, canonical 比对留 audit.py。"""
        pid = _validate_project_id(project_id)
        conn = self._conn

        def _foreign(table: str) -> list[str]:
            # 本 per-project 库里不该有别 project 的行(load_graph 已按 pid 过滤, 故串台只能查原表)。
            try:
                return [r[0] for r in conn.execute(f"SELECT DISTINCT project_id FROM {table}")
                        if r[0] != pid]
            except sqlite3.Error:
                return []

        def _soft_plugins(table: str, kinds: frozenset[str]) -> list[str]:
            # 软 kind 行上出现过的 plugin(audit.py 再滤掉 canonical = 孤儿漂移残留)。
            if not kinds:
                return []
            ph = ",".join("?" for _ in kinds)
            try:
                rows = conn.execute(
                    f"SELECT DISTINCT plugin FROM {table} WHERE project_id = ? AND kind IN ({ph})",
                    (pid, *kinds),
                ).fetchall()
            except sqlite3.Error:
                return []
            return sorted({r[0] for r in rows})

        return {
            "foreign_project_ids": {"nodes": _foreign("nodes"), "edges": _foreign("edges")},
            "soft_plugins": {
                "nodes": _soft_plugins("nodes", SOFT_NODE_KINDS),
                "edges": _soft_plugins("edges", SOFT_EDGE_KINDS),
            },
        }

    def list_project_ids(self) -> list[str]:
        """本 sqlite store 里出现过的 project_id(per-file 通常 = [本 pid], 串台时多)。"""
        rows = self._conn.execute("SELECT DISTINCT project_id FROM nodes").fetchall()
        return sorted(r[0] for r in rows)

    # ---- 生命周期 ----

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteGraphStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---- 工厂 + 枚举(后端选择的唯一边界; Stage B 在此接 PG 分派)----

def _graph_backend(cfg=None) -> str:
    """选 graph store 后端: config graph.store_backend(env > config > 'sqlite')。"""
    import os
    env = os.environ.get("CODEV_PLATFORM_GRAPH_BACKEND")
    if env:
        return env.strip().lower()
    from codev_platform.core.config import get, load_config
    cfg = cfg if cfg is not None else load_config()
    return (get(cfg, "graph.store_backend", default="sqlite") or "sqlite").strip().lower()


def _graph_pg_dsn(cfg=None) -> str | None:
    """PG dsn(复用 memory 全栈 PG 连接, env > config.memory.pg_dsn)。"""
    import os
    from codev_platform.core.config import get, load_config
    cfg = cfg if cfg is not None else load_config()
    return os.environ.get("CODEV_PLATFORM_MEMORY_DSN") or get(cfg, "memory.pg_dsn")


def open_store(project_id: str, *, mode: str = "rw", path: Path | None = None, cfg=None) -> GraphStore:
    """打开某 project 的统一图谱 store, 返回 GraphStore(消费方只认协议, 不碰底层连接)。

    后端选择只在此工厂一处(零 if-else 散落): config graph.store_backend(sqlite 默认 | pg)。
    - **显式 path → 必走 sqlite**(测试 / audit_all_stores 按文件路径开特定 .sqlite, 与后端配置无关)。
    - 未知 backend → **响亮硬失败**(数据进错库是静默腐败, 绝不默默降级 pg→sqlite)。
    - backend=pg 但缺 dsn / 缺 psycopg → 报错(operator 显式开了 pg, 要知道坏在哪, 不偷偷回 sqlite)。

    Args:
        project_id: 项目隔离键。
        mode:       'rw'(默认, 建/迁移/可写) | 'ro'(纯只读门禁)。pg 后端无 per-open 迁移, mode 仅 sqlite 用。
        path:       显式覆盖 DB 路径(测试 / 按文件审计);给了就走 sqlite。
        cfg:        显式 config(默认 load_config)。
    """
    if path is not None:
        return SqliteGraphStore(project_id, mode=mode, path=path)
    backend = _graph_backend(cfg)
    if backend == "sqlite":
        return SqliteGraphStore(project_id, mode=mode)
    if backend == "pg":
        from codev_platform.graph.pg_store import PgGraphStore
        dsn = _graph_pg_dsn(cfg)
        if not dsn:
            raise ValueError(
                "graph.store_backend=pg 但未配 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN")
        return PgGraphStore(dsn)
    raise ValueError(f"未知 graph.store_backend: {backend!r} (sqlite | pg)")


def list_project_ids() -> list[str]:
    """枚举已存在的 project(平台概览 / 跨项目审计用)。sqlite: glob data/graph_store/*.sqlite。"""
    d = data_root() / "graph_store"
    return sorted(p.stem for p in d.glob("*.sqlite")) if d.exists() else []
