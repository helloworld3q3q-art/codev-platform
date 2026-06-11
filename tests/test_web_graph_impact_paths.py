"""graph impact-paths 路由测试 (/api/v1/graph/impact-paths, Phase 5)。

mirror test_web_graph_soft_quality: seed 依赖链 store(前端→端点→函数→表)+ monkeypatch 路径,
经 TestClient 验 top-N 路径 + camelCase 字段映射 + 节点未找到 / store 缺失 graceful。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.graph.schema import (  # noqa: E402
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store  # noqa: E402
from codev_platform.web.routes import graph as graph_routes  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_PID = "ip"
_H = {"X-Project-Id": _PID}
_FE = f"{_PID}:frontend_api_call:src/UserPage.tsx:POST:/users"
_EP = f"{_PID}:backend_endpoint:POST:/users"
_FN = f"{_PID}:backend_function:repo.py:save_user"
_TB = f"{_PID}:db_table:users"


def _seed(store):
    c = open_store(_PID, path=store)
    nodes = [
        GraphNode(id=_FE, kind=NodeKind.FRONTEND_API_CALL.value, name="POST /users",
                  project_id=_PID, file="src/UserPage.tsx"),
        GraphNode(id=_EP, kind=NodeKind.BACKEND_ENDPOINT.value, name="create_user",
                  project_id=_PID, file="api.py"),
        GraphNode(id=_FN, kind=NodeKind.BACKEND_FUNCTION.value, name="save_user",
                  project_id=_PID, file="repo.py"),
        GraphNode(id=_TB, kind=NodeKind.DB_TABLE.value, name="users", project_id=_PID),
    ]
    edges = [
        GraphEdge(source=_FE, target=_EP, kind=EdgeKind.CALLS_API.value),
        GraphEdge(source=_EP, target=_FN, kind=EdgeKind.CALLS.value),
        GraphEdge(source=_FN, target=_TB, kind=EdgeKind.WRITES_TABLE.value),
    ]
    c.upsert_result(_PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


def _client(monkeypatch, store):
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=store, mode=mode))
    return TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG))


def test_impact_paths_reaches_dependents(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    _seed(store)
    r = _client(monkeypatch, store).post("/api/v1/graph/impact-paths",
                                         json={"nodeRef": "users"}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["found"] is True and d["target"]["name"] == "users"
    eps = {p["endpoint"]["id"] for p in d["paths"]}
    assert {_FN, _EP, _FE} <= eps          # 反向: 函数/端点/前端 都依赖 users
    fe_path = next(p for p in d["paths"] if p["endpoint"]["id"] == _FE)
    assert fe_path["hops"] and "viaEdge" in fe_path["hops"][0]  # snake via_edge → camel viaEdge 映射对
    assert "certain" in fe_path["hops"][0]


def test_impact_paths_node_not_found(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    _seed(store)
    r = _client(monkeypatch, store).post("/api/v1/graph/impact-paths",
                                         json={"nodeRef": "nonexistent_xyz"}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["found"] is False


def test_impact_paths_store_missing_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=tmp_path / "nope.sqlite", mode=mode))
    r = TestClient(build_app(title="t", routers=[graph_routes.router], cfg=_CFG)).post(
        "/api/v1/graph/impact-paths", json={"nodeRef": "users"}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["found"] is False
