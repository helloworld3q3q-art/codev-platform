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


def _seed(store, edges):
    c = open_store(_PID, path=store)
    nodes = [
        GraphNode(id="fn1", kind=NodeKind.BACKEND_FUNCTION.value, name="a",
                  project_id=_PID, file="a.py"),
        GraphNode(id="fn2", kind=NodeKind.BACKEND_FUNCTION.value, name="b",
                  project_id=_PID, file="b.py"),
    ]
    c.upsert_result(_PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


def _client(monkeypatch, store):
    # 路由经 open_store 工厂取 store(不再 graph_store_path); patch 工厂重定向到 seed 路径。
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=store, mode=mode))
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    return TestClient(app)


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


def test_graph_audit_store_missing_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_routes, "open_store",
                        lambda pid, mode="rw": open_store(pid, path=tmp_path / "nope.sqlite", mode=mode))
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    r = TestClient(app).post("/api/v1/graph/audit", json={}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["clean"] is True
