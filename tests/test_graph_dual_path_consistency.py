"""直读 SQLite(agent impact 工具)vs MCP dispatch 双路径一致性(codex 审计 P2)。

graph 两路本就同源: agent `tools/impact.py` 与 graph MCP `dispatch` 都读同一
`graph_store_path(pid)` + 调同一 `graph/impact.py` 引擎。此测**锁住接缝**, 防未来重构让
两路的 args 提取 / 路径路由悄悄分叉。纯函数, 不起 MCP server / 不连 daemon。
"""
from __future__ import annotations

from codev_platform.graph import impact as I
from codev_platform.graph import mcp_server as gm
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
)
from codev_platform.graph.store import open_store, upsert_result


def _seed(conn):
    e = GraphNode(id="p:backend_endpoint:GET /orders", kind=NodeKind.BACKEND_ENDPOINT,
                  name="GET /orders", project_id="p")
    t = GraphNode(id="p:db_table:orders", kind=NodeKind.DB_TABLE, name="orders", project_id="p")
    dom = GraphNode(id="p:business_domain:订单", kind=NodeKind.BUSINESS_DOMAIN,
                    name="订单", project_id="p", meta={"confidence": 0.7})
    se = GraphEdge(source=e.id, target=dom.id, kind=EdgeKind.BELONGS_TO_DOMAIN, confidence=0.7)
    upsert_result(conn, "p", AnalyzerResult(nodes=[e, t, dom], edges=[se], plugin="x"))


def test_graph_dual_path_same_result(tmp_path):
    # 不变量 A: 同一 store + 同一查询, agent 直读引擎(I.*)与 MCP dispatch 结果深度相等。
    # 锁住 dispatch 的 args 提取(query/ref/domain key)+ 默认值(kind=all,limit=50)不与直读分叉。
    conn = open_store("p", path=tmp_path / "g.sqlite")
    try:
        _seed(conn)
        assert (I.search_nodes(conn, "p", "order", "all", 50)
                == gm.dispatch("search_nodes", {"query": "order"}, conn, "p"))
        assert (I.find_node_domain(conn, "p", "GET /orders")
                == gm.dispatch("find_node_domain", {"ref": "GET /orders"}, conn, "p"))
        assert (I.list_domain_members(conn, "p", "订单")
                == gm.dispatch("list_domain_members", {"domain": "订单"}, conn, "p"))
    finally:
        conn.close()


def test_agent_direct_read_routes_via_graph_store_path(tmp_path, monkeypatch):
    # 不变量 B: agent 直读把 pid 路由到 graph_store_path 的文件 —— 与 MCP 同一路径真值源,
    # 不会读到另一个文件。锁住"两路同源"的唯一真实漂移点(路由解析分叉)。
    from codev_platform.graph import store as gs
    target = tmp_path / "routed.sqlite"
    monkeypatch.setattr(gs, "graph_store_path", lambda *a, **k: target)
    conn0 = open_store("p", path=target)
    upsert_result(conn0, "p", AnalyzerResult(
        nodes=[GraphNode(id="p:db_table:orders", kind=NodeKind.DB_TABLE,
                         name="orders", project_id="p")], plugin="x"))
    conn0.close()

    from codev_platform.agent.tools.impact import _open_store_ro
    conn, pid = _open_store_ro("p")  # 内部 graph_store_path(pid) 已被 monkeypatch → target
    try:
        assert pid == "p"
        hits = I.search_nodes(conn, "p", "orders", "all", 50)
        assert any(h["name"] == "orders" for h in hits["hits"])  # 读到 = 路由到了同一文件
    finally:
        conn.close()
