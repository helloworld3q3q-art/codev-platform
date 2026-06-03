"""Track A2 parity 工具单测 (tools/audit_graph_parity.py)。

造一个连通小图 store + 一个对应 cross_layer.sqlite, 验:
- 4 项指标分别正确统计 (endpoint / table / 前端链接 / 端点->表可达)
- store >= cross 时 retire_ok=True; 有缺口时 retire_ok=False + gaps 列差距
- 两库任一缺失时 graceful (available=False, 该侧计 0, 不抛栈)
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result

# 直接按文件加载 tools/audit_graph_parity.py (它在 tools/ 不在包内)。
_TOOL_PATH = Path(__file__).resolve().parent.parent / "tools" / "audit_graph_parity.py"
_spec = importlib.util.spec_from_file_location("audit_graph_parity", _TOOL_PATH)
agp = importlib.util.module_from_spec(_spec)
sys.modules["audit_graph_parity"] = agp
_spec.loader.exec_module(agp)

_PID = "p"


def _build_store(path: Path) -> None:
    """端点->函数(calls)->表 连通图, 1 个前端 calls_api, 2 表可达 (端点经函数碰 2 表)。"""
    fe = "p:frontend_api_call:UserPage:POST:/users"
    ep = "p:backend_endpoint:POST:/users"
    fn = "p:backend_function:repo.save_user"
    t1 = "p:db_table:users"
    t2 = "p:db_table:audit_log"
    nodes = [
        GraphNode(id=fe, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users", project_id=_PID),
        GraphNode(id=ep, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user", project_id=_PID),
        GraphNode(id=fn, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user", project_id=_PID),
        GraphNode(id=t1, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
        GraphNode(id=t2, kind=NodeKind.DB_TABLE.value, name="audit_log", project_id=_PID),
    ]
    edges = [
        GraphEdge(source=fe, target=ep, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=ep, target=fn, kind=EdgeKind.CALLS.value),
        GraphEdge(source=fn, target=t1, kind=EdgeKind.WRITES_TABLE.value),
        GraphEdge(source=fn, target=t2, kind=EdgeKind.WRITES_TABLE.value),
    ]
    conn = open_store(_PID, path=path)
    upsert_result(conn, _PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    conn.close()


def _build_cross(path: Path, *, endpoints: int, tables: int,
                 calls_api: int, method_table_pairs: int) -> None:
    """造 cross_layer.sqlite, 指定各计数 (用最少行数命中统计 SQL)。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE nodes (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, name TEXT, path TEXT);
        CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT, src_id INT, rel TEXT, dst_id INT);
        """
    )
    for i in range(endpoints):
        conn.execute("INSERT INTO nodes(kind, name, path) VALUES('java_endpoint', ?, NULL)", (f"ep{i}",))
    # table 节点 path IS NULL 才计入
    table_ids = []
    for i in range(tables):
        cur = conn.execute("INSERT INTO nodes(kind, name, path) VALUES('table', ?, NULL)", (f"t{i}",))
        table_ids.append(cur.lastrowid)
    for i in range(calls_api):
        conn.execute("INSERT INTO edges(src_id, rel, dst_id) VALUES(0, 'calls_api', 0)")
    # method->table 对: 造 method 节点 + queries_table 边
    for i in range(method_table_pairs):
        mcur = conn.execute("INSERT INTO nodes(kind, name, path) VALUES('java_method', ?, ?)",
                            (f"m{i}", f"M{i}.java"))
        tid = table_ids[i % tables] if tables else 0
        conn.execute("INSERT INTO edges(src_id, rel, dst_id) VALUES(?, 'queries_table', ?)",
                     (mcur.lastrowid, tid))
    conn.commit()
    conn.close()


def test_store_metrics_counts(tmp_path):
    store_db = tmp_path / "store.sqlite"
    _build_store(store_db)
    m = agp._store_metrics(_PID, store_db)
    assert m["available"] is True
    assert m["endpoints"] == 1
    assert m["tables"] == 2
    assert m["frontend_links"] == 1
    # 1 端点经函数碰 2 表 => 2 个 (endpoint, table) 可达对
    assert m["endpoint_table_reach"] == 2


def test_store_missing_graceful(tmp_path):
    m = agp._store_metrics(_PID, tmp_path / "nope.sqlite")
    assert m["available"] is False
    assert m["endpoints"] == 0 and m["endpoint_table_reach"] == 0


def test_cross_metrics_counts(tmp_path):
    cross_db = tmp_path / "cross.sqlite"
    _build_cross(cross_db, endpoints=3, tables=2, calls_api=4, method_table_pairs=2)
    conn = sqlite3.connect(cross_db)
    try:
        assert agp._scalar(conn, "SELECT COUNT(*) FROM nodes WHERE kind='java_endpoint'") == 3
        assert agp._cross_endpoint_table_reach(conn) == 2
    finally:
        conn.close()


def test_report_retire_ok_when_store_ge_cross():
    store = {"available": True, "path": "s", "endpoints": 5, "tables": 3,
             "frontend_links": 4, "endpoint_table_reach": 6}
    cross = {"available": True, "path": "c", "endpoints": 5, "tables": 2,
             "frontend_links": 4, "endpoint_table_reach": 5}
    rep = agp.build_report(_PID, store, cross)
    assert rep["retire_ok"] is True
    assert rep["gaps"] == []


def test_report_gap_when_store_below_cross():
    store = {"available": True, "path": "s", "endpoints": 5, "tables": 3,
             "frontend_links": 4, "endpoint_table_reach": 0}
    cross = {"available": True, "path": "c", "endpoints": 5, "tables": 2,
             "frontend_links": 4, "endpoint_table_reach": 185}
    rep = agp.build_report(_PID, store, cross)
    assert rep["retire_ok"] is False
    assert len(rep["gaps"]) == 1
    assert rep["gaps"][0]["metric"] == "endpoint_table_reach"
    assert rep["gaps"][0]["deficit"] == 185


def test_run_end_to_end(tmp_path, monkeypatch):
    """run() 走真实 store + cross 文件路径 (monkeypatch paths) 出报告。"""
    store_db = tmp_path / "store.sqlite"
    cross_db = tmp_path / "cross.sqlite"
    _build_store(store_db)
    _build_cross(cross_db, endpoints=1, tables=2, calls_api=1, method_table_pairs=2)
    monkeypatch.setattr(agp, "graph_store_path", lambda pid: store_db)
    monkeypatch.setattr(agp, "cross_link_db_path", lambda pid: cross_db)
    monkeypatch.setattr(agp, "cross_link_legacy_db_path", lambda: tmp_path / "legacy.sqlite")
    rep = agp.run(_PID)
    # store: ep=1,tbl=2,fe=1,reach=2; cross: ep=1,tbl=2,fe=1,reach=2 => 全 >=
    assert rep["retire_ok"] is True
