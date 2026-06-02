"""Graph 组测试 (plan §十五 Graph + §二十) —— codegraph + cross-link 只读 SQLite 包装。

覆盖 12 接口的代表子集 (cross-link stats/tables/table-refs + codegraph stats/search) 返回
200 统一 envelope; 字段形状对齐 Java codegraph-api。用临时 SQLite (含真 schema + 最小种子)
驱动只读查询, 经 monkeypatch 把 per-project 路径解析重定向到临时库 (不依赖平台真数据)。
另覆盖: DB 缺失 → index_missing(503) envelope; 空入参 → invalid_params(400)。

本 venv 未装 fastapi → importorskip 自动 skip。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.routes import graph as graph_routes  # noqa: E402

# passthrough → require_project_access 放行任意 X-Project-Id (acl advisory allow)。
_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_PID = "test-proj"
_HEADERS = {"X-Project-Id": _PID}


# ----------------------------------------------------------------------
# 临时 SQLite 种子 (schema 对齐真库)
# ----------------------------------------------------------------------


def _seed_codegraph(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE files (path TEXT, content_hash TEXT, language TEXT, size INTEGER,
            modified_at INTEGER, indexed_at INTEGER, node_count INTEGER, errors TEXT);
        CREATE TABLE nodes (id TEXT, kind TEXT, name TEXT, qualified_name TEXT, file_path TEXT,
            language TEXT, start_line INTEGER, end_line INTEGER, start_column INTEGER,
            end_column INTEGER, docstring TEXT, signature TEXT, visibility TEXT,
            is_exported INTEGER, is_async INTEGER, is_static INTEGER, is_abstract INTEGER,
            decorators TEXT, type_parameters TEXT, updated_at INTEGER);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, source TEXT, target TEXT, kind TEXT,
            metadata TEXT, line INTEGER, col INTEGER, provenance TEXT);
        CREATE VIRTUAL TABLE nodes_fts USING fts5(id, name, qualified_name, docstring, signature);
        """
    )
    c.execute("INSERT INTO files (path, language, size, node_count) VALUES ('a.py','python',100,2)")
    c.execute(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, "
        "is_exported, is_async) VALUES "
        "('n1','function','runDaily','mod.runDaily','a.py','python',10,1,0)"
    )
    c.execute(
        "INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line) "
        "VALUES ('n2','class','Runner','mod.Runner','a.py','python',1)"
    )
    c.execute("INSERT INTO edges (source, target, kind, line) VALUES ('n2','n1','contains',1)")
    c.execute("INSERT INTO nodes_fts (rowid, id, name) VALUES (1,'n1','runDaily')")
    c.execute("INSERT INTO nodes_fts (rowid, id, name) VALUES (2,'n2','Runner')")
    c.commit()
    c.close()


def _seed_cross_link(path: Path) -> None:
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE nodes (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, parent_id INTEGER,
            path TEXT, line INTEGER, language TEXT, meta_json TEXT);
        CREATE TABLE edges (id INTEGER PRIMARY KEY, src_id INTEGER, rel TEXT, dst_id INTEGER,
            confidence REAL, evidence TEXT);
        CREATE TABLE build_meta (key TEXT, value TEXT);
        """
    )
    # table node (path null) + a flyway definer + a java reader
    c.execute("INSERT INTO nodes (id, kind, name, path) VALUES (1,'table','stock_quote_daily',NULL)")
    c.execute("INSERT INTO nodes (id, kind, name, path, line) VALUES "
              "(2,'flyway_migration','V1__init',' db/V1__init.sql',1)")
    c.execute("INSERT INTO nodes (id, kind, name, path, line) VALUES "
              "(3,'java_method','QuoteMapper.select','Mapper.java',42)")
    c.execute("INSERT INTO edges (src_id, rel, dst_id, confidence, evidence) VALUES "
              "(2,'defines_table',1,1.0,'create table')")
    c.execute("INSERT INTO edges (src_id, rel, dst_id, confidence, evidence) VALUES "
              "(3,'queries_table',1,0.9,'select *')")
    c.execute("INSERT INTO build_meta (key, value) VALUES ('last_build_at','2026-06-02T00:00:00')")
    c.commit()
    c.close()


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    cg_db = tmp_path / "codegraph.db"
    cl_db = tmp_path / "cross_layer.sqlite"
    _seed_codegraph(cg_db)
    _seed_cross_link(cl_db)
    # 重定向 per-project 路径解析到临时库 (integration 模块内 import 的符号)。
    import codev_platform.web.integrations.codegraph_client as cgc
    import codev_platform.web.integrations.cross_link_client as clc
    monkeypatch.setattr(cgc, "codegraph_db_path", lambda pid: cg_db)
    monkeypatch.setattr(clc, "cross_link_db_path", lambda pid: cl_db)
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",))
    return TestClient(app)


# ----------------------------------------------------------------------
# cross-link
# ----------------------------------------------------------------------


def test_cross_link_stats_envelope(client):
    r = client.post("/api/v1/graph/cross-link/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0 and body["errors"] == []
    data = body["data"]
    assert data["lastBuildAt"] == "2026-06-02T00:00:00"
    assert data["nodesByKind"]["table"] == 1
    assert data["edgesByRel"]["queries_table"] == 1
    assert body["requestId"]


def test_cross_link_tables_envelope(client):
    r = client.post("/api/v1/graph/cross-link/tables", headers=_HEADERS, json={})
    assert r.status_code == 200
    assert r.json()["data"]["tables"] == ["stock_quote_daily"]


def test_cross_link_table_refs_shape(client):
    r = client.post("/api/v1/graph/cross-link/table-refs", headers=_HEADERS,
                    json={"table": "stock_quote_daily"})
    assert r.status_code == 200
    data = r.json()["data"]
    # 7 类 key 全在 (字段形状对齐 Java CrossLinkTableRefsResponse)
    for key in ("definers", "javaReaders", "javaWriters", "javaUpdaters",
                "pythonReaders", "pythonWriters", "pythonUpdaters"):
        assert key in data
    assert data["table"] == "stock_quote_daily"
    assert data["definers"][0]["name"] == "V1__init"
    assert data["javaReaders"][0]["name"] == "QuoteMapper.select"
    assert data["javaReaders"][0]["confidence"] == 0.9


def test_cross_link_table_refs_empty_table_is_invalid_params(client):
    r = client.post("/api/v1/graph/cross-link/table-refs", headers=_HEADERS, json={"table": ""})
    assert r.status_code == 400
    body = r.json()
    assert body["result"] == 1 and body["errors"][0]["errorCode"] == "invalid_params"


# ----------------------------------------------------------------------
# codegraph
# ----------------------------------------------------------------------


def test_codegraph_stats_envelope(client):
    r = client.post("/api/v1/graph/codegraph/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["totalNodes"] == 2
    assert data["totalEdges"] == 1
    assert data["totalFiles"] == 1
    assert data["byNodeKind"]["function"] == 1
    assert data["byLanguage"]["python"] == 2


def test_codegraph_search_shape(client):
    r = client.post("/api/v1/graph/codegraph/search", headers=_HEADERS,
                    json={"keyword": "run"})
    assert r.status_code == 200
    items = r.json()["data"]["items"]
    names = {it["name"] for it in items}
    assert "runDaily" in names
    hit = next(it for it in items if it["name"] == "runDaily")
    # camelCase 字段 + bool 归一 (对齐 Java NodeDTO)
    assert hit["qualifiedName"] == "mod.runDaily"
    assert hit["filePath"] == "a.py"
    assert hit["startLine"] == 10
    assert hit["isExported"] is True
    assert hit["isAsync"] is False


def test_codegraph_search_empty_keyword_is_invalid_params(client):
    r = client.post("/api/v1/graph/codegraph/search", headers=_HEADERS, json={"keyword": ""})
    assert r.status_code == 400
    assert r.json()["errors"][0]["errorCode"] == "invalid_params"


# ----------------------------------------------------------------------
# DB 缺失: 概览/可视化(stats/graph)优雅空(200); 具体查询(table-refs)仍 index_missing(503)
# ----------------------------------------------------------------------


def test_missing_db_stats_is_graceful_empty(tmp_path, monkeypatch):
    """无索引项目: stats 返回空(200 result:0), 前端显零而非报错 toast。"""
    import codev_platform.web.integrations.cross_link_client as clc
    monkeypatch.setattr(clc, "cross_link_db_path", lambda pid: tmp_path / "nope.sqlite")
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/cross-link/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == 0 and body["data"]["nodesByKind"] == {} and body["data"]["edgesByRel"] == {}


def test_missing_db_specific_query_still_index_missing(tmp_path, monkeypatch):
    """无索引时具体查询(table-refs)仍是显式 index_missing(503), 不被空状态吞掉。"""
    import codev_platform.web.integrations.cross_link_client as clc
    monkeypatch.setattr(clc, "cross_link_db_path", lambda pid: tmp_path / "nope.sqlite")
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/cross-link/table-refs", headers=_HEADERS, json={"table": "t"})
    assert r.status_code == 503
    assert r.json()["errors"][0]["errorCode"] == "index_missing"


# ----------------------------------------------------------------------
# 统一图谱 store (插件产出) 优先 + 空则 fallback (task2)
# ----------------------------------------------------------------------


def _seed_graph_store(path: Path, project_id: str) -> None:
    """往统一图谱 store 写一份 cross-link 风格产出 (meta 含 cross_link_kind/rel 留底)。"""
    from codev_platform.graph.schema import (
        AnalyzerResult,
        GraphEdge,
        GraphNode,
    )
    from codev_platform.graph.store import open_store, upsert_result

    table = GraphNode(
        id=f"{project_id}:table:1", kind="db_table", name="store_table",
        project_id=project_id, file=None, line=None, language=None,
        meta={"cross_link_kind": "table"},
    )
    endpoint = GraphNode(
        id=f"{project_id}:java_endpoint:2", kind="backend_endpoint",
        name="GET /api/x", project_id=project_id, file="X.java", line=10,
        language="java", meta={"cross_link_kind": "java_endpoint"},
    )
    edge = GraphEdge(
        source=endpoint.id, target=table.id, kind="reads_table", confidence=0.8,
        meta={"cross_link_rel": "queries_table"},
    )
    result = AnalyzerResult(
        nodes=[table, endpoint], edges=[edge], plugin="builtin.cross_link",
    )
    conn = open_store(project_id, path=path)
    try:
        upsert_result(conn, project_id, result)
    finally:
        conn.close()


def test_cross_link_graph_reads_store_when_present(tmp_path, monkeypatch):
    """store 有 cross-link 数据 → graph 路由消费 store (不读 cross_layer.sqlite)。"""
    store_db = tmp_path / "store.sqlite"
    _seed_graph_store(store_db, _PID)
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: store_db)
    # cross_layer.sqlite 故意缺失: 若误走 fallback 会变空, 测试就能抓到。
    import codev_platform.web.integrations.cross_link_client as clc
    monkeypatch.setattr(clc, "cross_link_db_path", lambda pid: tmp_path / "nope.sqlite")
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/cross-link/graph", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["nodeCount"] == 2 and data["edgeCount"] == 1
    kinds = {n["kind"] for n in data["nodes"]}
    assert kinds == {"table", "java_endpoint"}  # 还原成 cross-link 原始 kind
    assert data["edges"][0]["kind"] == "queries_table"  # 还原成 cross-link 原始 rel
    names = {n["name"] for n in data["nodes"]}
    assert names == {"store_table", "GET /api/x"}


def test_cross_link_stats_reads_store_when_present(tmp_path, monkeypatch):
    """store 有数据 → stats 路由按 cross-link 原始 kind/rel 统计 store。"""
    store_db = tmp_path / "store.sqlite"
    _seed_graph_store(store_db, _PID)
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: store_db)
    import codev_platform.web.integrations.cross_link_client as clc
    monkeypatch.setattr(clc, "cross_link_db_path", lambda pid: tmp_path / "nope.sqlite")
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/cross-link/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["nodesByKind"] == {"table": 1, "java_endpoint": 1}
    assert data["edgesByRel"] == {"queries_table": 1}


def test_cross_link_graph_falls_back_when_store_empty(client, tmp_path, monkeypatch):
    """store 缺失/空 → fallback 现有 cross_layer.sqlite (现有页面不破)。"""
    # store 路径指向不存在的文件 → 走 fallback。client fixture 已 seed cross_layer.sqlite。
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: tmp_path / "no_store.sqlite")
    r = client.post("/api/v1/graph/cross-link/graph", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    # fallback 读到 seed 的 cross_layer.sqlite: table + flyway + java_method 三节点。
    assert data["nodeCount"] >= 1
    kinds = {n["kind"] for n in data["nodes"]}
    assert "table" in kinds


def test_cross_link_stats_falls_back_when_store_empty(client, tmp_path, monkeypatch):
    """store 空 → stats fallback cross_layer.sqlite (build_meta.last_build_at 可见)。"""
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: tmp_path / "no_store.sqlite")
    r = client.post("/api/v1/graph/cross-link/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["lastBuildAt"] == "2026-06-02T00:00:00"
    assert data["nodesByKind"]["table"] == 1


# ----------------------------------------------------------------------
# operationId 唯一 (plan §十三)
# ----------------------------------------------------------------------


def test_no_duplicate_operation_ids():
    from codev_platform.core.httpkit import duplicate_operation_ids
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",))
    assert duplicate_operation_ids(app) == []
