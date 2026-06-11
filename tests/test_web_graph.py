"""Graph 组测试 (plan §十五 Graph + §二十) —— codegraph 只读 SQLite 包装 + 统一图谱 store。

覆盖 codegraph (stats/search) 返回 200 统一 envelope; 字段形状对齐 Java codegraph-api。
用临时 SQLite (含真 schema + 最小种子) 驱动只读查询, 经 monkeypatch 把 per-project 路径
解析重定向到临时库 (不依赖平台真数据)。统一图谱组直读 graph.store (多插件聚合)。

cross-link 组已于 2026-06-03 全栈血缘收敛退场 (跨业务链路并入统一图谱), 相关测试一并移除。

本 venv 未装 fastapi → importorskip 自动 skip。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.graph.store import open_store  # noqa: E402
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


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    cg_db = tmp_path / "codegraph.db"
    _seed_codegraph(cg_db)
    # 重定向 per-project 路径解析到临时库 (integration 模块内 import 的符号)。
    import codev_platform.web.integrations.codegraph_client as cgc
    monkeypatch.setattr(cgc, "codegraph_db_path", lambda pid: cg_db)
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",))
    return TestClient(app)


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


def test_build_fts_prefix_and_vs_or():
    from codev_platform.web.integrations.codegraph_client import CodegraphClient
    assert CodegraphClient._build_fts_prefix("weighted rrf") == '"weighted"* "rrf"*'       # 默认 AND(空格)
    assert CodegraphClient._build_fts_prefix("weighted rrf", match_mode="or") == '"weighted"* OR "rrf"*'
    assert CodegraphClient._build_fts_prefix("") == '""'


def test_codegraph_search_or_mode_finds_partial(tmp_path):
    # OR 模式: verbose 多词 query 混入不存在的描述词时, AND 全灭、OR 仍由 bm25 顶出目标。
    from codev_platform.web.integrations.codegraph_client import CodegraphClient
    cg_db = tmp_path / "cg.db"
    _seed_codegraph(cg_db)
    with CodegraphClient(db_path=cg_db) as cg:
        assert cg.search("run missing", None, None, 10, match_mode="and") == []   # 'missing' 不存在 → AND 灭
        names = {r["name"] for r in cg.search("run missing", None, None, 10, match_mode="or")}
        assert "runDaily" in names                                                # OR: 'run'* 仍命中


# ----------------------------------------------------------------------
# 统一图谱 store (全量节点/边, 所有插件)
# ----------------------------------------------------------------------


def _seed_unified_store(path: Path, project_id: str) -> None:
    """往 store 写多插件混合产出: database (db_table/db_column) + 后端端点。"""
    from codev_platform.graph.schema import AnalyzerResult, GraphEdge, GraphNode
    from codev_platform.graph.store import open_store

    table = GraphNode(
        id=f"{project_id}:db_table:t1", kind="db_table", name="stock_quote_daily",
        project_id=project_id, file="V1__init.sql", line=1, language="sql",
    )
    column = GraphNode(
        id=f"{project_id}:db_column:c1", kind="db_column", name="close_price",
        project_id=project_id, file="V1__init.sql", line=3, language="sql",
        meta={"data_type": "numeric"},
    )
    col_edge = GraphEdge(source=table.id, target=column.id, kind="contains")
    db_result = AnalyzerResult(
        nodes=[table, column], edges=[col_edge], plugin="builtin.sql",
    )
    endpoint = GraphNode(
        id=f"{project_id}:backend_endpoint:e1", kind="backend_endpoint",
        name="GET /api/quote", project_id=project_id, file="X.java", line=10,
        language="java",
    )
    read_edge = GraphEdge(source=endpoint.id, target=table.id, kind="reads_table")
    be_result = AnalyzerResult(
        nodes=[endpoint], edges=[read_edge], plugin="builtin.backend_spring",
    )
    conn = open_store(project_id, path=path)
    try:
        conn.upsert_result(project_id, db_result)
        conn.upsert_result(project_id, be_result)
    finally:
        conn.close()


def test_unified_graph_returns_all_plugin_nodes(tmp_path, monkeypatch):
    """统一图谱返回全部插件节点 (db_table/db_column + backend_endpoint), 统一 kind 直出。"""
    store_db = tmp_path / "store.sqlite"
    _seed_unified_store(store_db, _PID)
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=store_db, mode=mode))
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/unified/graph", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["nodeCount"] == 3 and data["edgeCount"] == 2
    kinds = {n["kind"] for n in data["nodes"]}
    assert kinds == {"db_table", "db_column", "backend_endpoint"}
    col = next(n for n in data["nodes"] if n["kind"] == "db_column")
    assert col["name"] == "close_price"
    assert col["meta"]["data_type"] == "numeric"  # meta 不透明往返
    edge_kinds = {e["kind"] for e in data["edges"]}
    assert edge_kinds == {"contains", "reads_table"}


def test_unified_stats_counts_by_kind(tmp_path, monkeypatch):
    """统一统计按 kind 聚合全部插件节点/边。"""
    store_db = tmp_path / "store.sqlite"
    _seed_unified_store(store_db, _PID)
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=store_db, mode=mode))
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    r = c.post("/api/v1/graph/unified/stats", headers=_HEADERS, json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["totalNodes"] == 3 and data["totalEdges"] == 2
    assert data["nodesByKind"] == {"db_table": 1, "db_column": 1, "backend_endpoint": 1}
    assert data["edgesByKind"] == {"contains": 1, "reads_table": 1}


def test_missing_project_header_is_400_not_500(client):
    """缺 X-Project-Id 打 project 范围路由 → 400 invalid_params(ProjectIdError 统一映射),
    不许漏到兜底 500 internal(2026-06-11 audit/soft-quality 曾因此 500)。"""
    for path in ("/api/v1/graph/audit", "/api/v1/graph/soft-quality"):
        r = client.post(path, json={})
        assert r.status_code == 400, path
        body = r.json()
        assert body["result"] == 1
        assert body["errors"][0]["errorCode"] == "invalid_params"


def test_unified_graph_empty_when_store_missing(tmp_path, monkeypatch):
    """store 缺失 → 统一图谱返回空 (200, 非错误)。"""
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=tmp_path / "nope.sqlite", mode=mode))
    c = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",)))
    rg = c.post("/api/v1/graph/unified/graph", headers=_HEADERS, json={})
    assert rg.status_code == 200
    assert rg.json()["result"] == 0 and rg.json()["data"]["nodeCount"] == 0
    rs = c.post("/api/v1/graph/unified/stats", headers=_HEADERS, json={})
    assert rs.status_code == 200
    assert rs.json()["data"]["totalNodes"] == 0 and rs.json()["data"]["nodesByKind"] == {}


# ----------------------------------------------------------------------
# operationId 唯一 (plan §十三)
# ----------------------------------------------------------------------


def test_no_duplicate_operation_ids():
    from codev_platform.core.httpkit import duplicate_operation_ids
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG, public_paths=("/health",))
    assert duplicate_operation_ids(app) == []
