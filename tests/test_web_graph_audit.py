"""graph audit 路由测试 (/api/v1/graph/audit, Phase 3)。

mirror test_web_reports: seed 一个统一图谱 store + monkeypatch 路径, 经 TestClient
验"clean / 断链检出 / store 缺失 graceful"。web 依赖(fastapi/sqlalchemy)缺则 skip。
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
_PID = "testproj"
_H = {"X-Project-Id": _PID}


def _seed(store, edges, nodes=None):
    c = open_store(_PID, path=store)
    base_nodes = nodes or [
        GraphNode(id="fn1", kind=NodeKind.BACKEND_FUNCTION.value, name="a",
                  project_id=_PID, file="a.py"),
        GraphNode(id="fn2", kind=NodeKind.BACKEND_FUNCTION.value, name="b",
                  project_id=_PID, file="b.py"),
    ]
    c.upsert_result(_PID, AnalyzerResult(nodes=base_nodes, edges=edges, plugin="test"))
    c.close()


def _client(monkeypatch, store):
    # 路由经 open_store 工厂取 store(不再 graph_store_path); patch 工厂重定向到 seed 路径。
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=store, mode=mode))
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    return TestClient(app)


def _frontend_api(name: str, url: str, method: str = "POST") -> GraphNode:
    return GraphNode(
        id=f"{_PID}:frontend_api_call:src/api.ts:{name}",
        kind=NodeKind.FRONTEND_API_CALL.value,
        name=name,
        project_id=_PID,
        file="src/api.ts",
        meta={"url": url, "http_method": method},
    )


def _backend_endpoint(name: str, url: str, method: str = "POST") -> GraphNode:
    return GraphNode(
        id=f"{_PID}:backend_endpoint:{method}:{url}",
        kind=NodeKind.BACKEND_ENDPOINT.value,
        name=name,
        project_id=_PID,
        file="Controller.java",
        meta={"url": url, "http_method": method},
    )


def test_graph_audit_clean(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    _seed(store, [GraphEdge(source="fn1", target="fn2", kind=EdgeKind.CALLS.value)])
    r = _client(monkeypatch, store).post("/api/v1/graph/audit", json={}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["clean"] is True and d["errorCount"] == 0 and d["nodes"] == 2


def test_graph_audit_detects_dangling(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    # 边指向不存在节点 → 断链 error
    _seed(store, [GraphEdge(source="fn1", target="ghost", kind=EdgeKind.CALLS.value)])
    r = _client(monkeypatch, store).post("/api/v1/graph/audit", json={}, headers=_H)
    d = r.json()["data"]
    assert d["clean"] is False and d["danglingEdges"] >= 1 and d["errorCount"] >= 1


def test_graph_audit_reports_api_link_coverage(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    fe_linked = _frontend_api("createOrder", "/orders")
    fe_unlinked = _frontend_api("deleteOrder", "/orders/delete")
    backend = _backend_endpoint("createOrder", "/orders")
    _seed(
        store,
        [GraphEdge(source=fe_linked.id, target=backend.id, kind=EdgeKind.CALLS_API.value)],
        [fe_linked, fe_unlinked, backend],
    )
    r = _client(monkeypatch, store).post("/api/v1/graph/audit", json={}, headers=_H)
    d = r.json()["data"]
    assert d["clean"] is True and d["errorCount"] == 0
    assert d["apiLinkStatus"] == "partial_frontend_linkage"
    assert d["apiLinkBrief"] == "api 1/2 linked (backend 1, calls_api 1)"
    assert d["frontendApiCalls"] == 2
    assert d["backendEndpoints"] == 1
    assert d["callsApiEdges"] == 1
    assert d["linkedFrontendApiCalls"] == 1
    assert d["unlinkedFrontendApiCalls"] == 1
    assert d["invalidCallsApiEdges"] == 0
    assert d["frontendLinkRatio"] == 0.5


def test_graph_audit_reports_fully_linked_api_coverage(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    frontend = _frontend_api("createOrder", "/orders")
    backend = _backend_endpoint("createOrder", "/orders")
    _seed(
        store,
        [GraphEdge(source=frontend.id, target=backend.id, kind=EdgeKind.CALLS_API.value)],
        [frontend, backend],
    )
    r = _client(monkeypatch, store).post("/api/v1/graph/audit", json={}, headers=_H)
    d = r.json()["data"]
    assert d["clean"] is True and d["errorCount"] == 0
    assert d["apiLinkStatus"] == "linked"
    assert d["apiLinkBrief"] == ""
    assert d["frontendApiCalls"] == 1
    assert d["linkedFrontendApiCalls"] == 1
    assert d["unlinkedFrontendApiCalls"] == 0
    assert d["frontendLinkRatio"] == 1.0


def test_graph_audit_reports_malformed_calls_api_as_warning(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    frontend = _frontend_api("getUser", "/users", method="GET")
    backend = _backend_endpoint("getUser", "/users", method="GET")
    service = GraphNode(
        id=f"{_PID}:backend_function:UserService",
        kind=NodeKind.BACKEND_FUNCTION.value,
        name="UserService",
        project_id=_PID,
        file="UserService.java",
    )
    _seed(
        store,
        [GraphEdge(source=frontend.id, target=service.id, kind=EdgeKind.CALLS_API.value)],
        [frontend, backend, service],
    )
    r = _client(monkeypatch, store).post("/api/v1/graph/audit", json={}, headers=_H)
    d = r.json()["data"]
    assert d["clean"] is True and d["errorCount"] == 0
    assert d["apiLinkStatus"] == "frontend_backend_unlinked"
    assert d["callsApiEdges"] == 0
    assert d["invalidCallsApiEdges"] == 1
    assert d["unlinkedFrontendApiCalls"] == 1


def test_graph_audit_store_missing_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=tmp_path / "nope.sqlite", mode=mode))
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    r = TestClient(app).post("/api/v1/graph/audit", json={}, headers=_H)
    d = r.json()["data"]
    assert r.status_code == 200 and d["clean"] is True
    assert d["apiLinkStatus"] == "no_frontend_api"
