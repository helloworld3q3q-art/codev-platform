"""graph store 节点级 project_id 纵深防御 —— 共享库隔离回归。

用显式共享 store_path 模拟"两个 project 落进同一 sqlite"的场景, 断言:
- 写时 nodes.project_id 强制用入参 (不被 GraphNode.project_id 污染)
- load_graph 始终按 project_id 过滤 (不串项目)
- upsert_result 清旧数据时按 (plugin, project_id) 删 (不误删别项目同插件节点)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from codev_platform.graph.schema import (
    AnalyzerResult,
    Evidence,
    Finding,
    GraphEdge,
    GraphNode,
)
from codev_platform.graph.store import load_graph, open_store, upsert_result


def _result(node_id: str, node_project_id: str) -> AnalyzerResult:
    """同插件 "px", 单节点; node 自带的 project_id 故意可与入参不同。"""
    return AnalyzerResult(
        nodes=[
            GraphNode(
                id=node_id,
                kind="backend_function",
                name=node_id,
                project_id=node_project_id,
            )
        ],
        plugin="px",
    )


def test_shared_store_isolates_projects(tmp_path: Path) -> None:
    shared = tmp_path / "shared.sqlite"
    ra = _result("a:fn", "proj-a")
    rb = _result("b:fn", "proj-b")

    conn = open_store("proj-a", path=shared)
    try:
        upsert_result(conn, "proj-a", ra)
        upsert_result(conn, "proj-b", rb)

        # load_graph("proj-a") 只回 proj-a 的节点。
        ga = load_graph(conn, "proj-a")
        assert [n.id for n in ga.nodes] == ["a:fn"]
        assert all(n.project_id == "proj-a" for n in ga.nodes)

        # load_graph("proj-b") 只回 proj-b 的节点。
        gb = load_graph(conn, "proj-b")
        assert [n.id for n in gb.nodes] == ["b:fn"]
        assert all(n.project_id == "proj-b" for n in gb.nodes)

        # 再次 upsert proj-a (同 plugin "px") 不能误删 proj-b 的节点。
        upsert_result(conn, "proj-a", ra)
        gb_after = load_graph(conn, "proj-b")
        assert [n.id for n in gb_after.nodes] == ["b:fn"]
    finally:
        conn.close()


def test_node_project_id_forced_from_param(tmp_path: Path) -> None:
    """node 自带的 project_id 与入参不一致时, 落盘 / 读回都以入参为准。"""
    shared = tmp_path / "shared.sqlite"
    # GraphNode.project_id 故意写成 "wrong", upsert 入参是 "proj-a"。
    rogue = _result("a:fn", "wrong")

    conn = open_store("proj-a", path=shared)
    try:
        upsert_result(conn, "proj-a", rogue)
        ga = load_graph(conn, "proj-a")
        assert [n.project_id for n in ga.nodes] == ["proj-a"]
        # 用错误的 project_id 读, 读不到 (没被污染到 "wrong" 桶)。
        gw = load_graph(conn, "wrong")
        assert gw.nodes == []
    finally:
        conn.close()


def _full_result(prefix: str) -> AnalyzerResult:
    """带 edges/evidences/findings 的产出, id 以 prefix 区分项目。

    两 project 同插件 "px" 的 evidences/findings seq 都从 0 起 —— C1 前 (主键不含
    project_id) 共享库下会主键冲突, 正是本测试要覆盖的点。
    """
    n1 = GraphNode(id=f"{prefix}:fn1", kind="backend_function", name="fn1",
                   project_id=prefix)
    n2 = GraphNode(id=f"{prefix}:fn2", kind="backend_function", name="fn2",
                   project_id=prefix)
    return AnalyzerResult(
        nodes=[n1, n2],
        edges=[GraphEdge(source=n1.id, target=n2.id, kind="calls")],
        evidences=[Evidence(source="px", detail=f"{prefix}-ev")],
        findings=[Finding(kind="impact", severity="info", title=f"{prefix}-find",
                          node_ids=[n1.id])],
        plugin="px",
    )


def test_shared_store_isolates_edges_evidences_findings(tmp_path: Path) -> None:
    """C1: 共享库下 edges/evidences/findings 也按 project_id 隔离 (不只 nodes)。"""
    shared = tmp_path / "shared.sqlite"
    conn = open_store("proj-a", path=shared)
    try:
        upsert_result(conn, "proj-a", _full_result("proj-a"))
        # proj-b 同插件、evidences/findings seq 同为 0: C1 主键纳入 project_id 后不撞。
        upsert_result(conn, "proj-b", _full_result("proj-b"))

        ga = load_graph(conn, "proj-a")
        assert [e.source for e in ga.edges] == ["proj-a:fn1"]
        assert [ev.detail for ev in ga.evidences] == ["proj-a-ev"]
        assert [f.title for f in ga.findings] == ["proj-a-find"]

        gb = load_graph(conn, "proj-b")
        assert [e.source for e in gb.edges] == ["proj-b:fn1"]
        assert [ev.detail for ev in gb.evidences] == ["proj-b-ev"]
        assert [f.title for f in gb.findings] == ["proj-b-find"]

        # 重 upsert proj-a (同插件) 不误删 proj-b 的 edges/evidences/findings。
        upsert_result(conn, "proj-a", _full_result("proj-a"))
        gb2 = load_graph(conn, "proj-b")
        assert [e.source for e in gb2.edges] == ["proj-b:fn1"]
        assert [ev.detail for ev in gb2.evidences] == ["proj-b-ev"]
        assert [f.title for f in gb2.findings] == ["proj-b-find"]
    finally:
        conn.close()


# C1 前 (edges/evidences/findings 无 project_id 列) 的旧表结构, 供迁移回归。
_LEGACY_DDL = """
CREATE TABLE nodes (id TEXT NOT NULL, plugin TEXT NOT NULL, kind TEXT NOT NULL,
    name TEXT NOT NULL, project_id TEXT NOT NULL, file TEXT, line INTEGER,
    language TEXT, meta_json TEXT, PRIMARY KEY (id, plugin));
CREATE TABLE edges (plugin TEXT NOT NULL, source TEXT NOT NULL, target TEXT NOT NULL,
    kind TEXT NOT NULL, confidence REAL DEFAULT 1.0, meta_json TEXT,
    PRIMARY KEY (plugin, source, target, kind));
CREATE TABLE evidences (plugin TEXT NOT NULL, seq INTEGER NOT NULL, source TEXT NOT NULL,
    detail TEXT NOT NULL, file TEXT, line INTEGER, confidence REAL DEFAULT 1.0,
    meta_json TEXT, PRIMARY KEY (plugin, seq));
CREATE TABLE findings (plugin TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
    severity TEXT NOT NULL, title TEXT NOT NULL, detail TEXT, node_ids_json TEXT,
    evidence_ids_json TEXT, meta_json TEXT, PRIMARY KEY (plugin, seq));
CREATE TABLE ingest_meta (plugin TEXT PRIMARY KEY, plugin_version TEXT, node_count INTEGER,
    edge_count INTEGER, evidence_count INTEGER, finding_count INTEGER, ingested_at TEXT);
"""


def test_legacy_schema_migrates_preserving_data(tmp_path: Path) -> None:
    """旧库 (三表无 project_id 列) open_store 时前向迁移: 加列 + 回填 pid + 不丢数据。"""
    db = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(db)
    try:
        raw.executescript(_LEGACY_DDL)
        raw.execute("INSERT INTO edges (plugin, source, target, kind) "
                    "VALUES ('px', 'a', 'b', 'calls')")
        raw.execute("INSERT INTO evidences (plugin, seq, source, detail) "
                    "VALUES ('px', 0, 's', 'ev-d')")
        raw.execute("INSERT INTO findings (plugin, seq, kind, severity, title) "
                    "VALUES ('px', 0, 'impact', 'info', 'find-t')")
        raw.commit()
    finally:
        raw.close()

    conn = open_store("proj-a", path=db)  # 触发迁移, 回填 project_id = "proj-a"
    try:
        for table in ("edges", "evidences", "findings"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            assert "project_id" in cols, f"{table} 迁移后应有 project_id 列"
        # 数据保留, 按回填的 pid 读得到。
        g = load_graph(conn, "proj-a")
        assert [e.source for e in g.edges] == ["a"]
        assert [ev.detail for ev in g.evidences] == ["ev-d"]
        assert [f.title for f in g.findings] == ["find-t"]
        # 回填正确: 换个 project_id 读不到。
        assert load_graph(conn, "proj-b").edges == []
    finally:
        conn.close()
