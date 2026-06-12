"""operationId 契约桥 + URL 多服务消歧 + 契约漂移(多仓 Phase 1)。

验收(plan 两服务 fixture, 不需真多仓): 同 url /api/list 跨两服务, operationId 桥连对服务不串;
无 operationId 时 URL 桥把跨服务候选降级标 ambiguous(不再静默连错 = 修潜伏 bug); 悬空调用被漂移检出。
语言/仓库数无关: 节点是中性 GraphNode, 单仓多仓同一套断言。
"""
from __future__ import annotations

import pytest

from codev_platform.graph.contract_drift import find_contract_drift
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store
from codev_platform.plugins.builtin._stack_scan import link_api_calls

_PID = "p"


def _fe(node_id: str, url: str, *, method: str = "POST", op: str | None = None, svc: str = "") -> GraphNode:
    meta = {"url": url, "http_method": method}
    if op:
        meta["operation_id"] = op
    if svc:
        meta["service"] = svc
    return GraphNode(id=node_id, kind=NodeKind.FRONTEND_API_CALL.value, name=url,
                     project_id=_PID, file="fe.ts", meta=meta)


def _ep(node_id: str, url: str, *, method: str = "POST", op: str | None = None, svc: str = "") -> GraphNode:
    meta = {"url": url, "http_method": method}
    if op:
        meta["operation_id"] = op
    if svc:
        meta["service"] = svc
    return GraphNode(id=node_id, kind=NodeKind.BACKEND_ENDPOINT.value, name=op or url,
                     project_id=_PID, file="be.py", meta=meta)


# ---- URL 桥: 单候选行为保留(向后兼容)----

def test_single_exact_match_conf_1():
    edges = link_api_calls([_fe("fe1", "/api/users")], [_ep("ep1", "/api/users")])
    assert len(edges) == 1 and edges[0].confidence == 1.0 and edges[0].target == "ep1"


def test_single_method_mismatch_conf_07():
    edges = link_api_calls([_fe("fe1", "/api/users", method="GET")], [_ep("ep1", "/api/users", method="POST")])
    assert len(edges) == 1 and edges[0].confidence == 0.7


def test_no_backend_no_edge():
    assert link_api_calls([_fe("fe1", "/api/nope")], [_ep("ep1", "/api/users")]) == []


# ---- URL 桥: 多服务消歧(修潜伏 bug)----

def test_same_url_two_services_url_only_is_ambiguous():
    """两服务同 POST /api/list, 前端仅按 url(无 operationId)→ 不再静默 0.7 连首个, 降 0.5 + ambiguous_service。"""
    backend = [
        _ep("svcA:ep", "/api/list", svc="user-svc"),
        _ep("svcB:ep", "/api/list", svc="order-svc"),
    ]
    edges = link_api_calls([_fe("fe1", "/api/list")], backend)
    assert len(edges) == 1
    assert edges[0].confidence == 0.5
    assert "ambiguous_service" in edges[0].meta["evidence"]


# ---- operationId 桥: 精确连对服务(repo 无关)----

def test_operation_id_bridge_picks_correct_service():
    """两服务同 url, 前端带 service-A 的 operationId → 精确连 A 的端点, 不串到 B。conf=1.0。"""
    backend = [
        _ep("svcA:list", "/api/list", op="userList", svc="user-svc"),
        _ep("svcB:list", "/api/list", op="orderList", svc="order-svc"),
    ]
    fe = _fe("fe1", "/api/list", op="userList", svc="user-svc")
    edges = link_api_calls([fe], backend)
    assert len(edges) == 1
    assert edges[0].target == "svcA:list" and edges[0].confidence == 1.0
    assert "operation_id" in edges[0].meta["evidence"]


def test_operation_id_takes_priority_over_url():
    """前端 operationId 命中后端, 即便 url 也能匹配别的, 仍走 operationId(优先级更高)。"""
    backend = [
        _ep("by_op", "/api/x", op="theOp", svc="s"),
        _ep("by_url", "/api/y", svc="s"),
    ]
    # 前端 url=/api/y(会 URL-命中 by_url)但带 op=theOp(精确命中 by_op)→ 取 by_op。
    fe = _fe("fe1", "/api/y", op="theOp", svc="s")
    edges = link_api_calls([fe], backend)
    assert len(edges) == 1 and edges[0].target == "by_op" and edges[0].confidence == 1.0


def test_operation_id_absent_falls_back_to_url():
    """无 operationId(单仓常见)→ 优雅退 URL 桥, 行为不变。"""
    edges = link_api_calls([_fe("fe1", "/api/z")], [_ep("ep1", "/api/z")])
    assert len(edges) == 1 and edges[0].confidence == 1.0


# ---- 契约漂移: 悬空前端调用 ----

def test_find_contract_drift_flags_dangling_call(tmp_path):
    """前端调 /api/gone 但后端无此端点 → 无 calls_api 出边 → 漂移检出; 正常连上的不报。"""
    c = open_store(_PID, path=tmp_path / "g.sqlite")
    fe_ok = _fe("fe_ok", "/api/users")
    fe_gone = _fe("fe_gone", "/api/gone")
    ep = _ep("ep1", "/api/users")
    edges = [GraphEdge(source="fe_ok", target="ep1", kind=EdgeKind.CALLS_API.value)]
    c.upsert_result(_PID, AnalyzerResult(nodes=[fe_ok, fe_gone, ep], edges=edges, plugin="test"))
    r = find_contract_drift(c, _PID)
    assert r["found"] and r["count"] == 1
    ids = {d["id"] for d in r["danglingCalls"]}
    assert ids == {"fe_gone"}
    assert r["danglingCalls"][0]["url"] == "/api/gone"
