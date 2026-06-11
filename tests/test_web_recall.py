"""recall 路由测试 (/api/v1/recall/code, Phase 6)。

seed graph store + monkeypatch open_store; codegraph db 不存在 → fail-soft → graph-only。
web 依赖缺则 skip。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.graph.schema import AnalyzerResult, GraphNode, NodeKind  # noqa: E402
from codev_platform.graph.store import open_store as real_open_store  # noqa: E402
from codev_platform.graph.store import upsert_result  # noqa: E402
from codev_platform.web.routes import recall as recall_routes  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}
_PID = "testproj"
_H = {"X-Project-Id": _PID}


def _app():
    return build_app(title="t", routers=[recall_routes.router], cfg=_CFG)


def test_recall_code_graph_only_fail_soft(tmp_path, monkeypatch):
    store = tmp_path / "g.sqlite"
    c = real_open_store(_PID, path=store)
    c.upsert_result(_PID, AnalyzerResult(
        nodes=[GraphNode(id="fn:save_user", kind=NodeKind.BACKEND_FUNCTION.value,
                         name="save_user", project_id=_PID, file="repo.py"),
               GraphNode(id="t:orders", kind=NodeKind.DB_TABLE.value,
                         name="orders", project_id=_PID)],
        plugin="test"))
    c.close()
    # codegraph db 不存在 → 该 lane fail-soft, graph lane 仍出结果。
    monkeypatch.setattr("codev_platform.graph.store.open_store",
                        lambda pid: real_open_store(pid, path=store))
    r = TestClient(_app()).post("/api/v1/recall/code", json={"query": "user"}, headers=_H)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["count"] == 1                       # 只 save_user 含 "user"
    assert d["hits"][0]["name"] == "save_user" and d["hits"][0]["kind"] == "backend_function"
    assert d["hits"][0]["lanes"] == ["graph"]
    assert d["lanes"] == ["graph"]               # 实际参与 lane 可观测


def test_recall_code_empty_query_graceful():
    # 空 query → recall_code 早返回空(不触 lane), 非错误。
    r = TestClient(_app()).post("/api/v1/recall/code", json={"query": "  "}, headers=_H)
    assert r.status_code == 200 and r.json()["data"]["count"] == 0
