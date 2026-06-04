"""跨插件链接: 按 URL (+ method 容差) 匹配前端调用 -> 后端端点, 建 calls_api 边。"""
from __future__ import annotations

from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
)


def link_api_calls(
    frontend_api_nodes: list[GraphNode],
    backend_endpoint_nodes: list[GraphNode],
) -> list[GraphEdge]:
    """按 URL (+ method 容差) 匹配 frontend_api_call -> backend_endpoint, 建 calls_api 边。

    精确 url+method 命中 confidence=1.0; url 命中但 method 不一致 confidence=0.7
    (仍建边便于发现, evidence 标注)。单一真值源: 任何栈插件组合都调本函数。
    """
    by_url: dict[str, list[tuple[str, str]]] = {}
    for n in backend_endpoint_nodes:
        url = str(n.meta.get("url") or "").strip()
        if not url:
            continue
        method = str(n.meta.get("http_method") or "POST").upper()
        by_url.setdefault(url, []).append((n.id, method))

    edges: list[GraphEdge] = []
    for n in frontend_api_nodes:
        url = str(n.meta.get("url") or "").strip()
        if not url:
            continue
        method = str(n.meta.get("http_method") or "POST").upper()
        candidates = by_url.get(url)
        if not candidates:
            continue
        chosen = None
        for ep_id, ep_method in candidates:
            if ep_method == method:
                chosen = (ep_id, 1.0, "exact")
                break
        if chosen is None:
            ep_id, ep_method = candidates[0]
            chosen = (ep_id, 0.7, f"method_mismatch front={method} back={ep_method}")
        ep_id, conf, tag = chosen
        edges.append(
            GraphEdge(
                source=n.id,
                target=ep_id,
                kind=EdgeKind.CALLS_API.value,
                confidence=conf,
                meta={"evidence": f"{tag} url={url}"},
            )
        )
    return edges
