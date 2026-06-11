"""影响分析 agent 工具单测 (agent/tools/impact.py)。

seed 连通 store + monkeypatch graph_store_path, 验 4 工具 run() 产出 + 注册进默认 registry。
"""
from __future__ import annotations

import json

from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.impact import (
    ApiCallersTool,
    ImpactAnalysisTool,
    ImpactPathsTool,
    PageDependenciesTool,
    TableUsageTool,
)
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store

_PID = "tp"
_FE = f"{_PID}:frontend_api_call:src/UserPage.tsx:POST:/users"
_EP = f"{_PID}:backend_endpoint:POST:/users"
_FN = f"{_PID}:backend_function:repo.py:save_user"
_TB = f"{_PID}:db_table:users"


def _seed(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    c = open_store(_PID, path=store)
    nodes = [
        GraphNode(id=_FE, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users", project_id=_PID,
                  file="src/UserPage.tsx"),
        GraphNode(id=_EP, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user", project_id=_PID,
                  file="api.py"),
        GraphNode(id=_FN, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user", project_id=_PID,
                  file="repo.py"),
        GraphNode(id=_TB, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
    ]
    edges = [
        GraphEdge(source=_FE, target=_EP, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=_EP, target=_FN, kind=EdgeKind.CALLS.value),
        GraphEdge(source=_FN, target=_TB, kind=EdgeKind.WRITES_TABLE.value),
    ]
    c.upsert_result(_PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()
    import codev_platform.graph.store as gs
    monkeypatch.setattr(gs, "graph_store_path", lambda pid: store)


def test_impact_analysis_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    res = ImpactAnalysisTool(_PID).run({"nodeRef": "users"})
    assert res.is_error is False
    d = json.loads(res.content)
    assert d["found"] and d["risk"] == "high" and d["total"] == 3


def test_table_usage_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    res = TableUsageTool(_PID).run({"table": "Users"})
    d = json.loads(res.content)
    assert d["found"]
    ids = {n["id"] for layer in d["usage"]["byLayer"].values() for n in layer}
    assert ids == {_EP, _FN, _FE}


def test_api_callers_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    res = ApiCallersTool(_PID).run({"endpointRef": _EP})
    d = json.loads(res.content)
    assert d["found"] and d["count"] == 1 and d["callers"][0]["id"] == _FE


def test_page_dependencies_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    res = PageDependenciesTool(_PID).run({"pageRef": _FE})
    d = json.loads(res.content)
    assert {n["id"] for n in d["dependsOn"]["byLayer"]["database"]} == {_TB}


def test_impact_paths_tool(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    res = ImpactPathsTool(_PID).run({"nodeRef": "users"})
    assert res.is_error is False
    d = json.loads(res.content)
    assert d["found"] and d["target"]["name"] == "users"
    eps = {p["endpoint"]["id"] for p in d["paths"]}
    assert {_FN, _EP, _FE} <= eps          # 反向: 函数/端点/前端 都依赖 users
    fe = next(p for p in d["paths"] if p["endpoint"]["id"] == _FE)
    assert fe["hops"] and "certain" in fe["hops"][0]  # 逐跳带可解释字段


def test_missing_arg_is_error():
    res = ImpactAnalysisTool(_PID).run({})
    assert res.is_error is True


def test_store_missing_is_error(tmp_path, monkeypatch):
    import codev_platform.graph.store as gs
    monkeypatch.setattr(gs, "graph_store_path", lambda pid: tmp_path / "nope.sqlite")
    res = TableUsageTool(_PID).run({"table": "users"})
    assert res.is_error is True and "不存在" in res.content


def test_registered_in_default_registry():
    reg = build_default_registry(_PID)
    for name in ("impact_analysis", "table_usage", "page_dependencies", "api_callers", "impact_paths"):
        assert reg.get(name) is not None
