"""platform_status 软标签摘要单测(_soft_quality_summary)——

给平台状态 per-project 聚合提供 A1/A2 软标签健康摘要。只读、永不抛: 未建/读失败优雅降级。
"""
from __future__ import annotations

from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store
from codev_platform.platform_status import _soft_quality_summary

PID = "t-plstatus"


def _seed(store, nodes, edges):
    c = open_store(PID, path=store)
    c.upsert_result(PID, AnalyzerResult(nodes=nodes, edges=edges, plugin="test"))
    c.close()


def _ep(i):
    return GraphNode(id=f"e{i}", kind=NodeKind.BACKEND_ENDPOINT.value,
                     name=f"GET /api/{i}", project_id=PID, file=f"api{i}.py")


def _domain(name):
    return GraphNode(id=f"{PID}:business_domain:{name}", kind=NodeKind.BUSINESS_DOMAIN.value,
                     name=name, project_id=PID, meta={"confidence": 0.8})


def _belongs(hid, dom):
    return GraphEdge(source=hid, target=f"{PID}:business_domain:{dom}",
                     kind=EdgeKind.BELONGS_TO_DOMAIN.value, confidence=0.8)


def test_not_built_when_no_store(tmp_path):
    assert _soft_quality_summary(tmp_path / "nope.sqlite", PID) == "not_built"


def test_summary_healthy(tmp_path):
    store = tmp_path / "g.sqlite"
    _seed(store, [_ep(1), _ep(2), _domain("订单")],
          [_belongs("e1", "订单"), _belongs("e2", "订单")])
    s = _soft_quality_summary(store, PID)
    assert s["healthy"] is True and s["flags"] == 0 and s["domains"] == 1 and s["source"] == "local"


def test_summary_flags_giant(tmp_path):
    store = tmp_path / "g.sqlite"
    _seed(store, [_ep(1), _ep(2), _ep(3), _ep(4), _domain("巨型"), _domain("小")],
          [_belongs("e1", "巨型"), _belongs("e2", "巨型"), _belongs("e3", "巨型"),
           _belongs("e4", "小")])
    s = _soft_quality_summary(store, PID)
    assert s["healthy"] is False and s["flags"] >= 1 and s["domains"] == 2


def test_no_soft_layer_healthy(tmp_path):
    store = tmp_path / "g.sqlite"
    _seed(store, [_ep(1), _ep(2)], [])
    s = _soft_quality_summary(store, PID)
    assert s["healthy"] is True and s["domains"] == 0 and s["layers"] == 0
