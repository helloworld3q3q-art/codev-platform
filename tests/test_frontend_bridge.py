"""前端内部桥接(frontend_module → 同文件 api_call/route)纯逻辑单测。

修两个前端插件 id 不相交致 frontend_module 孤岛、impact 滤软边后到不了后端的 bug。
"""
from __future__ import annotations

from codev_platform.graph.ingest import build_frontend_bridge_edges
from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind

PID = "p"


def _n(nid, kind, file):
    return GraphNode(id=nid, kind=kind, name=nid, project_id=PID, file=file)


def test_bridges_module_to_same_file_children():
    nodes = [
        _n("mod:x", NodeKind.FRONTEND_MODULE.value, "web/x.ts"),
        _n("api:x", NodeKind.FRONTEND_API_CALL.value, "web/x.ts"),   # 同文件 → 应连
        _n("route:x", NodeKind.FRONTEND_ROUTE.value, "web/x.ts"),    # 同文件 → 应连
        _n("mod:y", NodeKind.FRONTEND_MODULE.value, "web/y.ts"),
        _n("api:z", NodeKind.FRONTEND_API_CALL.value, "web/z.ts"),   # 无同文件 module → 不连
    ]
    edges = build_frontend_bridge_edges(nodes)
    pairs = {(e.source, e.target, e.kind) for e in edges}
    assert ("mod:x", "api:x", EdgeKind.CONTAINS.value) in pairs
    assert ("mod:x", "route:x", EdgeKind.CONTAINS.value) in pairs
    # z 无同文件 module → 不产桥
    assert not any(e.target == "api:z" for e in edges)
    # 全是 contains 硬边 + conf 1.0
    assert all(e.kind == EdgeKind.CONTAINS.value and e.confidence == 1.0 for e in edges)
    assert len(edges) == 2


def test_no_self_loop_and_dedup():
    # module 自己不连自己; 同对不重复
    nodes = [
        _n("mod:x", NodeKind.FRONTEND_MODULE.value, "web/x.ts"),
        _n("api:x", NodeKind.FRONTEND_API_CALL.value, "web/x.ts"),
    ]
    edges = build_frontend_bridge_edges(nodes + nodes)  # 重复输入
    assert len(edges) == 1
    assert all(e.source != e.target for e in edges)


def test_backend_nodes_ignored():
    nodes = [
        _n("mod:x", NodeKind.FRONTEND_MODULE.value, "web/x.ts"),
        _n("fn", NodeKind.BACKEND_FUNCTION.value, "web/x.ts"),   # 后端节点不桥
        _n("ep", NodeKind.BACKEND_ENDPOINT.value, "web/x.ts"),
    ]
    assert build_frontend_bridge_edges(nodes) == []
