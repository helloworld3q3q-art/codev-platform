"""统一图谱 MCP server dispatch 测试 —— 纯函数 dispatch(绕过 MCP 装饰器)。

验证 8 个工具的 name→impact 查询映射 + A1 业务域查询经 MCP 入口可达。SSE/auth 部署样板
照搬 cross-link(已在那侧验证), 这里只钉 dispatch 契约。
"""
from __future__ import annotations

import pytest

from codev_platform.graph import mcp_server as gm
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store


def test_dispatch_table_has_thirteen_tools():
    assert len(gm._DISPATCH) == 13   # 8 (impact+A1) + 3 (A2) + recall_code(P6) + find_impact_paths(P5)
    assert "find_impact_paths" in gm._DISPATCH
    assert "find_node_domain" in gm._DISPATCH
    assert "search_nodes" in gm._DISPATCH
    assert "list_domain_members" in gm._DISPATCH
    assert {"find_arch_role", "list_layer_members", "find_arch_violations"} <= set(gm._DISPATCH)
    assert "recall_code" in gm._DISPATCH


def test_dispatch_recall_code_serializes(monkeypatch):
    # recall_code 工具: 自开 store(忽略传入 conn=None), 序列化 CodeRecallHit → {hits,count,lanes}。
    from codev_platform.recall.service import CodeRecallHit
    monkeypatch.setattr(
        "codev_platform.recall.recall_code",
        lambda q, pid, **kw: [CodeRecallHit(ref="r1", score=0.5, name="foo", kind="fn",
                                            file="a.py", lanes=["graph"])])
    r = gm.dispatch("recall_code", {"query": "foo"}, None, "p")
    assert r["count"] == 1 and r["lanes"] == ["graph"]
    assert r["hits"][0]["name"] == "foo" and r["hits"][0]["ref"] == "r1"


def test_dispatch_recall_code_missing_query_raises():
    with pytest.raises(KeyError):
        gm.dispatch("recall_code", {}, None, "p")   # 缺 query


def test_dispatch_unknown_tool_raises():
    with pytest.raises(ValueError):
        gm.dispatch("nope", {}, None, "p")


def test_dispatch_missing_arg_raises():
    with pytest.raises(KeyError):
        gm.dispatch("find_node_domain", {}, None, "p")  # 缺 ref


def test_dispatch_business_domain_queries(tmp_path):
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        e = GraphNode(id="p:backend_endpoint:GET /orders", kind=NodeKind.BACKEND_ENDPOINT,
                      name="GET /orders", project_id="p")
        dom = GraphNode(id="p:business_domain:订单", kind=NodeKind.BUSINESS_DOMAIN,
                        name="订单", project_id="p", meta={"confidence": 0.7})
        se = GraphEdge(source=e.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN,
                       confidence=0.7)
        conn.upsert_result("p", AnalyzerResult(nodes=[e, dom], edges=[se], plugin="x"))

        r = gm.dispatch("find_node_domain", {"ref": "GET /orders"}, conn, "p")
        assert r["found"] and r["domains"] == ["订单"]
        m = gm.dispatch("list_domain_members", {"domain": "订单"}, conn, "p")
        assert m["found"] and m["count"] == 1
    finally:
        conn.close()


def test_dispatch_search_nodes(tmp_path):
    # search_nodes 承接 cross-link: 模糊搜 + kind 过滤(退役 cross-link 后由 graph MCP 提供)。
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        e = GraphNode(id="p:backend_endpoint:GET /orders", kind=NodeKind.BACKEND_ENDPOINT,
                      name="GET /orders", project_id="p")
        t = GraphNode(id="p:db_table:orders", kind=NodeKind.DB_TABLE, name="orders",
                      project_id="p")
        conn.upsert_result("p", AnalyzerResult(nodes=[e, t], plugin="x"))
        r = gm.dispatch("search_nodes", {"query": "order"}, conn, "p")
        assert {h["name"] for h in r["hits"]} == {"GET /orders", "orders"}
        r2 = gm.dispatch("search_nodes", {"query": "order", "kind": "db_table"}, conn, "p")
        assert [h["name"] for h in r2["hits"]] == ["orders"]
    finally:
        conn.close()
