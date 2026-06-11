"""PgGraphStore —— GraphStore 的 PostgreSQL 实现(多机共享统一图谱真值源)。

替代单机 SqliteGraphStore。语义与 sqlite 实现严格对齐(契约测试 sqlite/pg 双跑守护),
消费方零改(只依赖 graph.store.GraphStore Protocol):
- upsert_result: 按 (project_id, plugin) 维度先删后写, **整体包一个事务**(多表替换原子 ——
  sqlite 靠单 conn.commit, PG pool 默认每句自动提交会留半写, 必须显式 with conn.transaction())。
  edges / ingest_meta 的"重灌替换"用 ON CONFLICT DO UPDATE(对应 sqlite 的 INSERT OR REPLACE)。
- load_graph / stats / audit_scan / list_project_ids: 按 project_id 过滤(共享库行隔离)。
- 读路径中途失败 → 抛中性 GraphStoreUnreadable(同 sqlite, 消费方不必 import psycopg)。

**与 sqlite schema 的唯一差异**: ingest_meta 加 project_id 列且纳入 PK —— sqlite 是 per-file
库(文件即 project 边界, ingest_meta 无 project_id), 共享 PG 库必须按 project_id 隔离否则
不同 project 同 plugin 的计数撞 PK。其余四表 schema 同构。

**org_id 多组织隔离暂未加**(与已落地的 reindex_jobs 一致, 走 project_id): 留到统一多组织阶段
(reindex_jobs/graph/memory 一起加 org_id + token 身份), 避免 piecemeal 漂移。

连接复用 memory/pg_queue 全栈 PG 范式(psycopg_pool ConnectionPool, open=False lazy)。
缺 psycopg_pool → 构造期 ImportError(open_store 工厂据此回退 / 报错)。
"""
from __future__ import annotations

import logging

from codev_platform.graph.schema import (
    SOFT_EDGE_KINDS,
    SOFT_NODE_KINDS,
    AnalyzerResult,
)
from codev_platform.graph.store import (
    _EDGE_COLS,
    _EVIDENCE_COLS,
    _FINDING_COLS,
    _NODE_COLS,
    GraphStoreUnreadable,
    _dump_meta,
    _row_to_edge,
    _row_to_evidence,
    _row_to_finding,
    _row_to_node,
)

logger = logging.getLogger(__name__)

_POOL_TIMEOUT_SEC = 5

# 5 表 DDL(PG 方言)。四表与 sqlite 同构; ingest_meta 加 project_id 入 PK(共享库隔离)。
_SCHEMA_DDL = (
    "CREATE TABLE IF NOT EXISTS graph_nodes ("
    "  id TEXT NOT NULL, plugin TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL,"
    "  project_id TEXT NOT NULL, file TEXT, line INTEGER, language TEXT, meta_json TEXT,"
    "  PRIMARY KEY (id, plugin))",
    "CREATE TABLE IF NOT EXISTS graph_edges ("
    "  project_id TEXT NOT NULL, plugin TEXT NOT NULL, source TEXT NOT NULL,"
    "  target TEXT NOT NULL, kind TEXT NOT NULL, confidence DOUBLE PRECISION DEFAULT 1.0,"
    "  meta_json TEXT, PRIMARY KEY (project_id, plugin, source, target, kind))",
    "CREATE TABLE IF NOT EXISTS graph_evidences ("
    "  project_id TEXT NOT NULL, plugin TEXT NOT NULL, seq INTEGER NOT NULL,"
    "  source TEXT NOT NULL, detail TEXT NOT NULL, file TEXT, line INTEGER,"
    "  confidence DOUBLE PRECISION DEFAULT 1.0, meta_json TEXT,"
    "  PRIMARY KEY (project_id, plugin, seq))",
    "CREATE TABLE IF NOT EXISTS graph_findings ("
    "  project_id TEXT NOT NULL, plugin TEXT NOT NULL, seq INTEGER NOT NULL,"
    "  kind TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL, detail TEXT,"
    "  node_ids_json TEXT, evidence_ids_json TEXT, meta_json TEXT,"
    "  PRIMARY KEY (project_id, plugin, seq))",
    "CREATE TABLE IF NOT EXISTS graph_ingest_meta ("
    "  project_id TEXT NOT NULL, plugin TEXT NOT NULL, plugin_version TEXT,"
    "  node_count INTEGER, edge_count INTEGER, evidence_count INTEGER,"
    "  finding_count INTEGER, ingested_at TEXT, PRIMARY KEY (project_id, plugin))",
    "CREATE INDEX IF NOT EXISTS ix_graph_nodes_kind ON graph_nodes (project_id, kind)",
    "CREATE INDEX IF NOT EXISTS ix_graph_nodes_name ON graph_nodes (project_id, name)",
    "CREATE INDEX IF NOT EXISTS ix_graph_edges_source ON graph_edges (project_id, source)",
    "CREATE INDEX IF NOT EXISTS ix_graph_edges_target ON graph_edges (project_id, target)",
)


class PgGraphStore:
    """GraphStore 的 PG 实现。缺 psycopg_pool → 构造期 ImportError(调用方回退/报错)。"""

    def __init__(self, dsn: str, *, max_size: int = 4) -> None:
        from psycopg_pool import ConnectionPool  # 缺 → ImportError
        self._pool = ConnectionPool(dsn, min_size=1, max_size=max_size, open=False,
                                    timeout=_POOL_TIMEOUT_SEC)
        self._opened = False

    def _ensure(self) -> None:
        if self._opened:
            return
        self._pool.open()
        with self._pool.connection() as conn, conn.transaction():
            for ddl in _SCHEMA_DDL:
                conn.execute(ddl)
        self._opened = True

    def probe(self) -> None:
        """轻量探活(open_store 工厂 fail-soft 用): 建连 + 建表; PG 不可达则短超时内抛。"""
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

    # ---- 读 ----

    def load_graph(self, project_id: str, *, plugin: str | None = None) -> AnalyzerResult:
        from codev_platform.core.project_id import validate as _v
        project_id = _v(project_id)
        where = "WHERE project_id = %s"
        params: tuple = (project_id,)
        if plugin:
            where += " AND plugin = %s"
            params = (project_id, plugin)
        try:
            self._ensure()   # 连接/建表失败也走中性异常(原在 try 外会泄漏 PoolTimeout)
            with self._pool.connection() as conn:
                nodes = [_row_to_node(r) for r in conn.execute(
                    f"SELECT {_NODE_COLS} FROM graph_nodes {where} ORDER BY id", params)]
                edges = [_row_to_edge(r) for r in conn.execute(
                    f"SELECT {_EDGE_COLS} FROM graph_edges {where} ORDER BY source, target, kind", params)]
                evidences = [_row_to_evidence(r) for r in conn.execute(
                    f"SELECT {_EVIDENCE_COLS} FROM graph_evidences {where} ORDER BY plugin, seq", params)]
                findings = [_row_to_finding(r) for r in conn.execute(
                    f"SELECT {_FINDING_COLS} FROM graph_findings {where} ORDER BY plugin, seq", params)]
                out = AnalyzerResult(nodes=nodes, edges=edges, evidences=evidences, findings=findings)
                if plugin:
                    out.plugin = plugin
                    row = conn.execute(
                        "SELECT plugin_version FROM graph_ingest_meta "
                        "WHERE project_id = %s AND plugin = %s", (project_id, plugin)).fetchone()
                    if row:
                        out.plugin_version = row[0]
                return out
        except Exception as exc:  # noqa: BLE001 — psycopg 各异常 → 中性, 消费方不必 import psycopg
            raise GraphStoreUnreadable(str(exc)) from exc

    # ---- 写 ----

    def upsert_result(self, project_id: str, result: AnalyzerResult) -> None:
        from codev_platform.core.project_id import validate as _v
        project_id = _v(project_id)
        self._ensure()
        plugin = result.plugin or "(unknown)"
        version = result.plugin_version or ""
        import json
        from datetime import datetime, timezone
        # 整体事务: 多表"先删后写"原子(PG pool 默认每句 autocommit, 不显式事务会留半写图谱)。
        with self._pool.connection() as conn, conn.transaction():
            # 同 (project,plugin) 写串行(审计 P1): advisory xact lock 事务结束自动释放。多机并发
            # upsert 同 key 时 evidences/findings/ingest_meta 是纯 INSERT(无 ON CONFLICT), READ
            # COMMITTED 下两事务 delete-then-insert 互不可见 → 后提交者撞 PK 抛 UniqueViolation。
            # 串行化让同 key upsert 排队(语义对齐"同 plugin 重灌本应串行"), 不同 key 不互斥。
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"{project_id}/{plugin}",))
            for t in ("graph_nodes", "graph_edges", "graph_evidences", "graph_findings"):
                conn.execute(f"DELETE FROM {t} WHERE plugin = %s AND project_id = %s",
                             (plugin, project_id))
            conn.execute("DELETE FROM graph_ingest_meta WHERE plugin = %s AND project_id = %s",
                         (plugin, project_id))
            if result.nodes:
                conn.cursor().executemany(
                    "INSERT INTO graph_nodes (id, plugin, kind, name, project_id, file, line, "
                    "language, meta_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (id, plugin) DO UPDATE SET kind=EXCLUDED.kind, name=EXCLUDED.name, "
                    "project_id=EXCLUDED.project_id, file=EXCLUDED.file, line=EXCLUDED.line, "
                    "language=EXCLUDED.language, meta_json=EXCLUDED.meta_json",
                    [(n.id, plugin, n.kind, n.name, project_id, n.file, n.line, n.language,
                      _dump_meta(n.meta)) for n in result.nodes])
            if result.edges:
                conn.cursor().executemany(
                    "INSERT INTO graph_edges (project_id, plugin, source, target, kind, "
                    "confidence, meta_json) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (project_id, plugin, source, target, kind) DO UPDATE SET "
                    "confidence=EXCLUDED.confidence, meta_json=EXCLUDED.meta_json",
                    [(project_id, plugin, e.source, e.target, e.kind, e.confidence,
                      _dump_meta(e.meta)) for e in result.edges])
            if result.evidences:
                conn.cursor().executemany(
                    "INSERT INTO graph_evidences (project_id, plugin, seq, source, detail, file, "
                    "line, confidence, meta_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(project_id, plugin, i, ev.source, ev.detail, ev.file, ev.line, ev.confidence,
                      _dump_meta(ev.meta)) for i, ev in enumerate(result.evidences)])
            if result.findings:
                conn.cursor().executemany(
                    "INSERT INTO graph_findings (project_id, plugin, seq, kind, severity, title, "
                    "detail, node_ids_json, evidence_ids_json, meta_json) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(project_id, plugin, i, f.kind, f.severity, f.title, f.detail,
                      json.dumps(f.node_ids, ensure_ascii=False),
                      json.dumps(f.evidence_ids, ensure_ascii=False), _dump_meta(f.meta))
                     for i, f in enumerate(result.findings)])
            conn.execute(
                "INSERT INTO graph_ingest_meta (project_id, plugin, plugin_version, node_count, "
                "edge_count, evidence_count, finding_count, ingested_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (project_id, plugin, version, len(result.nodes), len(result.edges),
                 len(result.evidences), len(result.findings),
                 datetime.now(timezone.utc).isoformat()))

    # ---- 概览 / 审计 ----

    def stats(self, project_id: str) -> dict:
        from codev_platform.core.project_id import validate as _v
        project_id = _v(project_id)
        try:
            self._ensure()
            with self._pool.connection() as conn:
                counts = {
                    short: int(conn.execute(
                        f"SELECT COUNT(*) FROM graph_{tbl} WHERE project_id = %s", (project_id,)
                    ).fetchone()[0])
                    for short, tbl in (("nodes", "nodes"), ("edges", "edges"),
                                       ("evidences", "evidences"), ("findings", "findings"))
                }
                plugins = [
                    {"plugin": r[0], "plugin_version": r[1], "node_count": r[2], "edge_count": r[3],
                     "evidence_count": r[4], "finding_count": r[5], "ingested_at": r[6]}
                    for r in conn.execute(
                        "SELECT plugin, plugin_version, node_count, edge_count, evidence_count, "
                        "finding_count, ingested_at FROM graph_ingest_meta WHERE project_id = %s "
                        "ORDER BY plugin", (project_id,))
                ]
            return {"totals": counts, "plugins": plugins}
        except Exception as exc:  # noqa: BLE001 — 读失败中性化(契约: 消费方只 catch GraphStoreUnreadable)
            raise GraphStoreUnreadable(str(exc)) from exc

    def audit_scan(self, project_id: str) -> dict:
        """后端探查。**foreign_project_ids 恒空**: "串台泄漏"是 sqlite **per-file** 隔离概念
        (每个 .sqlite 本应只装一个 project, 出现别 project_id 行 = 写错文件的 bug)。共享 PG 库
        所有 project 合法共表、隔离靠 WHERE project_id —— 别 project 的行是正常邻居非泄漏, 若按
        "project_id != pid"判会把每个别 project 都误报成串台。soft_plugins 漂移在两后端都成立。"""
        from codev_platform.core.project_id import validate as _v
        pid = _v(project_id)

        def _soft_plugins(tbl: str, kinds, conn) -> list[str]:
            if not kinds:
                return []
            rows = conn.execute(
                f"SELECT DISTINCT plugin FROM graph_{tbl} WHERE project_id = %s AND kind = ANY(%s)",
                (pid, list(kinds))).fetchall()
            return sorted({r[0] for r in rows})

        try:
            self._ensure()
            with self._pool.connection() as conn:
                return {
                    "foreign_project_ids": {"nodes": [], "edges": []},   # 共享库无 per-file 串台概念
                    "soft_plugins": {"nodes": _soft_plugins("nodes", SOFT_NODE_KINDS, conn),
                                     "edges": _soft_plugins("edges", SOFT_EDGE_KINDS, conn)},
                }
        except Exception as exc:  # noqa: BLE001 — 读失败中性化
            raise GraphStoreUnreadable(str(exc)) from exc

    def list_project_ids(self) -> list[str]:
        try:
            self._ensure()
            with self._pool.connection() as conn:
                rows = conn.execute("SELECT DISTINCT project_id FROM graph_nodes").fetchall()
            return sorted(r[0] for r in rows)
        except Exception as exc:  # noqa: BLE001 — 读失败中性化
            raise GraphStoreUnreadable(str(exc)) from exc

    # ---- 生命周期 ----

    def close(self) -> None:
        if self._opened:
            try:
                self._pool.close()
            except Exception:  # noqa: BLE001
                pass
            self._opened = False

    def __enter__(self) -> "PgGraphStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
