"""契约漂移检测 —— 悬空前端调用(frontend_api_call 无 calls_api 出边)。

从 impact 引擎拆出(file-discipline §1: impact.py 控行数; 契约漂移是独立关注点)。
自包含: 只依赖公开的 build_impact_graph + schema, 不碰 impact 私有 helper。

语言 / 框架 / 仓库数无关: 只看中性 frontend_api_call 节点 + calls_api 边。前后端分离 / 多服务 /
多仓项目改后端接口后, 用它确认前端没留断头调用(接口删 / 改签名 / operationId 漂移 / URL 写错)。
"""
from __future__ import annotations

from codev_platform.graph.impact import build_impact_graph, layer_of
from codev_platform.graph.schema import EdgeKind, GraphNode, NodeKind


def _drift_brief(n: GraphNode) -> dict:
    """悬空前端节点的定位 brief + 它"想调谁"的线索(供人核对后端少了哪个端点)。"""
    brief = {"id": n.id, "kind": n.kind, "name": n.name, "layer": layer_of(n.kind),
             "file": n.file, "line": n.line}
    if n.meta:
        for k in ("url", "http_method", "operation_id", "service"):
            if n.meta.get(k):
                brief[k] = n.meta[k]
    return brief


def _has_calls_api(g, node_id: str) -> bool:
    return any(kind == EdgeKind.CALLS_API.value for _t, kind in g.fwd.get(node_id, ()))


def find_contract_drift(store, project_id: str, *, limit: int = 200) -> dict:
    """契约漂移: 列出悬空前端调用 —— frontend_api_call 节点无**确定** calls_api 出边。

    两档(都是真要核对的契约问题, 分开是因根因不同):
    - danglingCalls: 一条 calls_api 边都没有 = 后端没这接口 / URL 写错 / 接口删了。
    - uncertainCalls: 只有**低置信(<0.7)兜底边**(同 URL 多服务时 URL 兜底误连被消歧降到 0.5)=
      连上了但"连的哪个服务不确定"。**此前被当已连接而静默漏报**(P2 修复): 0.5 边也满足旧的
      "any calls_api 即非漂移", 把多服务误连藏掉了。现用 certain_only 图区分: 有确定边才算真连上。
    每条带前端节点 brief + 尝试调的 url/operation_id; 各档截断在 limit。
    """
    g = build_impact_graph(store, project_id)
    g_certain = build_impact_graph(store, project_id, certain_only=True)
    dangling: list[dict] = []
    uncertain: list[dict] = []
    for n in g.nodes.values():
        if n.kind != NodeKind.FRONTEND_API_CALL.value:
            continue
        if _has_calls_api(g_certain, n.id):
            continue  # 有确定后端端点边, 真连上, 非漂移
        bucket = uncertain if _has_calls_api(g, n.id) else dangling
        if len(bucket) < limit:
            bucket.append(_drift_brief(n))
    for b in (dangling, uncertain):
        b.sort(key=lambda d: (d.get("file") or "", d.get("line") or 0))
    return {"found": True, "projectId": project_id,
            "danglingCalls": dangling, "count": len(dangling), "truncated": len(dangling) >= limit,
            "uncertainCalls": uncertain, "uncertainCount": len(uncertain)}
