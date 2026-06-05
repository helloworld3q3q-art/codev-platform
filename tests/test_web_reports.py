"""Reports 组路由测试 (Track A5 影响分析)。

seed 一个连通统一图谱 store (前端→端点→函数→表), monkeypatch store 路径,
经 TestClient 验 4 个影响分析路由 + store 缺失 graceful-empty。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform import platform_status  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.graph.schema import (  # noqa: E402
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result  # noqa: E402
from codev_platform.web.routes import reports as reports_routes  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_PID = "testproj"
_FE = f"{_PID}:frontend_api_call:src/UserPage.tsx:POST:/users"
_EP = f"{_PID}:backend_endpoint:POST:/users"
_FN = f"{_PID}:backend_function:repo.py:save_user"
_TB = f"{_PID}:db_table:users"
_H = {"X-Project-Id": _PID}


@pytest.fixture
def client(tmp_path, monkeypatch):
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
    upsert_result(c, _PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()
    monkeypatch.setattr(reports_routes, "graph_store_path", lambda pid: store)
    app = build_app(title="t", routers=[reports_routes.router], cfg=_CFG)
    return TestClient(app)


def test_impact_of_table_crosses_layers(client):
    r = client.post("/api/v1/reports/impact", json={"nodeRef": "users"}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["found"] and d["total"] == 3
    assert set(d["layersAffected"]) == {"frontend", "backend"}
    assert d["risk"] == "high"  # 触及前端 + 跨 2 层
    assert "users" in d["summary"]


def test_table_usage(client):
    r = client.post("/api/v1/reports/table-usage", json={"table": "Users"}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["found"]
    ids = {n["id"] for layer in d["data"]["usage"]["byLayer"].values() for n in layer}
    assert ids == {_EP, _FN, _FE}


def test_page_dependencies(client):
    r = client.post("/api/v1/reports/page-dependencies", json={"pageRef": _FE}, headers=_H)
    d = r.json()["data"]
    assert d["found"]
    deps = d["data"]["dependsOn"]["byLayer"]
    assert {n["id"] for n in deps["database"]} == {_TB}


def test_api_callers(client):
    r = client.post("/api/v1/reports/api-callers", json={"endpointRef": _EP}, headers=_H)
    d = r.json()["data"]
    assert d["found"] and d["data"]["count"] == 1
    assert d["data"]["callers"][0]["id"] == _FE


def test_impact_unknown_node(client):
    r = client.post("/api/v1/reports/impact", json={"nodeRef": "ghost"}, headers=_H)
    assert r.json()["data"]["found"] is False


def test_store_missing_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(reports_routes, "graph_store_path", lambda pid: tmp_path / "nope.sqlite")
    app = build_app(title="t", routers=[reports_routes.router], cfg=_CFG)
    r = TestClient(app).post("/api/v1/reports/impact", json={"nodeRef": "users"}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["found"] is False


# ---- GET /reports/mcp-usage: platform_admin 鉴权门 + 响应形状 ----

_FAKE_METRICS = {"chroma": {"agentCalls": 1, "devCalls": 2, "agentHits": 1, "devHits": 2},
                 "codegraph": {"calls": 1},
                 "model": {"agentEmbed": 1, "devEmbed": 2, "agentRerank": 1, "devRerank": 0}}


def _mcp_client(monkeypatch):
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": ["super"]})
    monkeypatch.setattr(platform_status, "mcp_usage_report", lambda repo: {
        "last7d": {"projects": [{"projectId": "p1", **_FAKE_METRICS}], "total": _FAKE_METRICS},
        "allTime": {"projects": [{"projectId": "p1", **_FAKE_METRICS}], "total": _FAKE_METRICS},
    })
    session_store.clear()
    return TestClient(build_app(title="t", routers=[reports_routes.router], cfg=_CFG))


def _bearer(username: str, org_id: str = "") -> dict:
    t = session_store.create(username, org_id)
    return {"Authorization": f"Bearer {t.access_token}"}


def test_mcp_usage_platform_admin_ok(monkeypatch):
    c = _mcp_client(monkeypatch)
    r = c.get("/api/v1/reports/mcp-usage", headers=_bearer("super"))
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["last7d"]["projects"][0]["chroma"]["agentCalls"] == 1
    assert d["last7d"]["total"]["model"]["devEmbed"] == 2
    assert "allTime" in d


def test_mcp_usage_non_admin_denied(monkeypatch):
    c = _mcp_client(monkeypatch)
    r = c.get("/api/v1/reports/mcp-usage", headers=_bearer("alice", "acme"))
    assert r.status_code == 403
    assert r.json()["errors"][0]["errorCode"] == "access_denied"
