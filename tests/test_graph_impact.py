"""影响分析引擎单测 (graph/impact.py)。

造一个连通小图 前端 --calls_api--> 端点 --calls--> 函数 --reads_table--> 表,
验 4 个查询方向 (impact 反向 / table_usage 反向 / page_deps 正向 / api_callers 反向取前端)。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.impact import (
    find_api_callers,
    find_impact,
    find_page_dependencies,
    find_table_usage,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result

_PID = "p"
_FE = "p:frontend_api_call:src/UserPage.tsx:POST:/users"
_EP = "p:backend_endpoint:POST:/users"
_FN = "p:backend_function:repo.py:save_user"
_TB = "p:db_table:users"


@pytest.fixture
def conn(tmp_path):
    c = open_store(_PID, path=tmp_path / "g.sqlite")
    nodes = [
        GraphNode(id=_FE, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users", project_id=_PID,
                  file="src/UserPage.tsx"),
        GraphNode(id=_EP, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user", project_id=_PID,
                  file="api.py", meta={"handler": "create_user"}),
        GraphNode(id=_FN, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user", project_id=_PID,
                  file="repo.py"),
        GraphNode(id=_TB, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
    ]
    edges = [
        GraphEdge(source=_FE, target=_EP, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=_EP, target=_FN, kind=EdgeKind.CALLS.value),
        GraphEdge(source=_FN, target=_TB, kind=EdgeKind.WRITES_TABLE.value),
    ]
    upsert_result(c, _PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    return c


def test_find_impact_of_table_reaches_all_upper_layers(conn):
    r = find_impact(conn, _PID, _TB)
    assert r["found"]
    layers = r["impact"]["byLayer"]
    assert {n["id"] for n in layers["backend"]} == {_EP, _FN}
    assert {n["id"] for n in layers["frontend"]} == {_FE}
    assert r["impact"]["total"] == 3  # function + endpoint + frontend


def test_find_table_usage_by_name(conn):
    r = find_table_usage(conn, _PID, "Users")  # 大小写不敏感
    assert r["found"] and r["table"]["id"] == _TB
    ids = {n["id"] for layer in r["usage"]["byLayer"].values() for n in layer}
    assert ids == {_EP, _FN, _FE}


def test_find_page_dependencies_forward(conn):
    r = find_page_dependencies(conn, _PID, _FE)
    assert r["found"]
    layers = r["dependsOn"]["byLayer"]
    assert {n["id"] for n in layers["backend"]} == {_EP, _FN}
    assert {n["id"] for n in layers["database"]} == {_TB}


def test_find_api_callers_returns_frontend(conn):
    r = find_api_callers(conn, _PID, _EP)
    assert r["found"] and r["count"] == 1
    assert r["callers"][0]["id"] == _FE


def test_unknown_ref_not_found(conn):
    assert find_impact(conn, _PID, "nope")["found"] is False
    assert find_table_usage(conn, _PID, "ghost")["found"] is False


def test_ambiguous_name_returns_candidates(tmp_path):
    # 两个同名端点 list (不同文件) -> 按 name 解析歧义, 回候选; 用 id 才能消歧
    c = open_store("p2", path=tmp_path / "g2.sqlite")
    n1 = GraphNode(id="p2:backend_endpoint:GET:/a/list", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="list", project_id="p2", file="a.py")
    n2 = GraphNode(id="p2:backend_endpoint:GET:/b/list", kind=NodeKind.BACKEND_ENDPOINT.value,
                   name="list", project_id="p2", file="b.py")
    upsert_result(c, "p2", AnalyzerResult(nodes=[n1, n2], plugin="test"))
    r = find_api_callers(c, "p2", "list")
    assert r["found"] is False and len(r["ambiguous"]) == 2
    assert {a["file"] for a in r["ambiguous"]} == {"a.py", "b.py"}
    # 精确 id 仍可解析
    assert find_api_callers(c, "p2", n1.id)["found"] is True
