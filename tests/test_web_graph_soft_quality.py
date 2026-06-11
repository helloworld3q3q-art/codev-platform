"""graph soft-quality 路由测试 (/api/v1/graph/soft-quality)。

mirror test_web_graph_audit: seed 软标签 store + monkeypatch 路径, 经 TestClient 验
"healthy / 巨型 cluster 检出 / store 缺失 graceful"。web 依赖缺则 skip。
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
from codev_platform.graph.store import open_store, upsert_result  # noqa: E402
from codev_platform.web.routes import graph as graph_routes  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_PID = "testproj"
_H = {"X-Project-Id": _PID}


def _ep(i: int) -> GraphNode:
    return GraphNode(id=f"e{i}", kind=NodeKind.BACKEND_ENDPOINT.value,
                     name=f"GET /api/{i}", project_id=_PID, file=f"api{i}.py")


def _domain(name: str) -> GraphNode:
    return GraphNode(id=f"{_PID}:business_domain:{name}", kind=NodeKind.BUSINESS_DOMAIN.value,
                     name=name, project_id=_PID, meta={"confidence": 0.8})


def _belongs(hid: str, dom: str) -> GraphEdge:
    return GraphEdge(source=hid, target=f"{_PID}:business_domain:{dom}",
                     kind=EdgeKind.BELONGS_TO_DOMAIN.value, confidence=0.8)


def _seed(store, nodes, edges):
    c = open_store(_PID, path=store)
    c.upsert_result(_PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


def _client(monkeypatch, store):
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: store)
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    return TestClient(app)


def test_soft_quality_healthy(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    _seed(store, [_ep(1), _ep(2), _domain("订单")],
          [_belongs("e1", "订单"), _belongs("e2", "订单")])
    r = _client(monkeypatch, store).post("/api/v1/graph/soft-quality", json={}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["healthy"] is True and d["flagCount"] == 0
    assert d["domainCount"] == 1 and d["domainCoverage"] == 1.0 and d["domainGiant"] == 0


def test_soft_quality_detects_giant(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    # 一域吃 3/4 → 巨型退化
    _seed(store, [_ep(1), _ep(2), _ep(3), _ep(4), _domain("巨型"), _domain("小")],
          [_belongs("e1", "巨型"), _belongs("e2", "巨型"), _belongs("e3", "巨型"),
           _belongs("e4", "小")])
    r = _client(monkeypatch, store).post("/api/v1/graph/soft-quality", json={}, headers=_H)
    d = r.json()["data"]
    assert d["healthy"] is False and d["domainGiant"] == 1
    assert any("巨型 cluster" in f for f in d["flags"])


def test_soft_quality_store_missing_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_routes, "graph_store_path", lambda pid: tmp_path / "nope.sqlite")
    app = build_app(title="t", routers=[graph_routes.router], cfg=_CFG)
    r = TestClient(app).post("/api/v1/graph/soft-quality", json={}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["healthy"] is True
