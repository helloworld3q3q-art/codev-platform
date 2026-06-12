"""跨插件链接: 前端调用 -> 后端端点, 建 calls_api 边。

**语言 / 框架 / 仓库数无关**: 本层只认中性 GraphNode.meta(url / http_method / operation_id /
service), 不含任何语言分支。各栈 scanner(fastapi/spring/node/react/vue ...)各自按本语言约定
填这些中性字段; 单仓单服务时 service 缺省 "" 即可, 多仓 / 多微服务才填 —— 同一套链接码两种都吃。

resolver 链(声明式, winner = 首个命中):
  1. operationId 精确桥: (service, operation_id) join, repo 无关, conf=1.0。两侧都带 operation_id
     才生效(前端经生成的 OpenAPI 客户端拿到 operationId, 后端 scanner 派生); 缺则跳过此条。
  2. URL 桥(兜底): 按 url(+method 容差)匹配。**多服务消歧**: 同 url 跨多个 service 的候选 →
     降 conf + evidence ambiguous_service(不再静默 candidates[0] 连错服务, 这是多服务潜伏 bug 的根因)。
"""
from __future__ import annotations

from codev_platform.graph.schema import (
    EdgeKind,
    GraphEdge,
    GraphNode,
)


def _meta_str(n: GraphNode, key: str, default: str = "") -> str:
    return str(n.meta.get(key) or default).strip()


def _index_by_operation(backend_nodes: list[GraphNode]) -> dict[tuple[str, str], list[str]]:
    """(service, operation_id) -> [endpoint_id]。无 operation_id 的端点不进此索引。"""
    idx: dict[tuple[str, str], list[str]] = {}
    for n in backend_nodes:
        op = _meta_str(n, "operation_id")
        if not op:
            continue
        idx.setdefault((_meta_str(n, "service"), op), []).append(n.id)
    return idx


def _index_by_url(backend_nodes: list[GraphNode]) -> dict[str, list[tuple[str, str, str]]]:
    """url -> [(endpoint_id, http_method, service)]。candidates 按 id 排序保确定性。"""
    idx: dict[str, list[tuple[str, str, str]]] = {}
    for n in backend_nodes:
        url = _meta_str(n, "url")
        if not url:
            continue
        method = _meta_str(n, "http_method", "POST").upper()
        idx.setdefault(url, []).append((n.id, method, _meta_str(n, "service")))
    for url in idx:
        idx[url].sort(key=lambda t: t[0])
    return idx


def _resolve_by_operation(
    fn: GraphNode, by_op: dict[tuple[str, str], list[str]],
) -> tuple[str, float, str] | None:
    """前端节点带 operation_id(+service)→ 精确命中后端端点。缺 operation_id / 未命中 → None。"""
    op = _meta_str(fn, "operation_id")
    if not op:
        return None
    targets = by_op.get((_meta_str(fn, "service"), op))
    if not targets:
        return None
    # 同 (service, operation_id) 理应唯一; 多个取确定性首个(已排序), evidence 标注。
    tag = "operation_id" if len(targets) == 1 else "operation_id ambiguous(同 opid 多端点)"
    return sorted(targets)[0], 1.0, f"{tag} op={op}"


def _resolve_by_url(
    fn: GraphNode, by_url: dict[str, list[tuple[str, str, str]]],
) -> tuple[str, float, str] | None:
    """前端 url(+method)→ 后端端点。多服务 / 多候选消歧: 降 conf + 标 ambiguous, 不静默连错。

    - 唯一候选 method 一致 → conf=1.0(exact);
    - 唯一候选 method 不一致 → conf=0.7(method_mismatch, 仍建边便于发现);
    - 多候选: 先按 method 收窄, 仍 >1 → 候选不确定。跨多个 service → conf=0.5 + ambiguous_service
      (多服务串台高危, 标到最低可信); 同一 service 内多候选 → conf=0.6 + ambiguous_endpoint。
    """
    url = _meta_str(fn, "url")
    if not url:
        return None
    candidates = by_url.get(url)
    if not candidates:
        return None
    method = _meta_str(fn, "http_method", "POST").upper()
    pool = [c for c in candidates if c[1] == method] or candidates
    if len(pool) == 1:
        ep_id, ep_method, _svc = pool[0]
        if ep_method == method:
            return ep_id, 1.0, f"exact url={url}"
        return ep_id, 0.7, f"method_mismatch front={method} back={ep_method} url={url}"
    # 多候选: 确定性取首个(已排序)但降 conf + 标不确定来源, 交给 certain_only 过滤掉(不当确定依赖)。
    services = {svc for _, _, svc in pool}
    ep_id, _ep_method, _svc = pool[0]
    if len(services) > 1:
        return ep_id, 0.5, f"ambiguous_service url={url} services={sorted(services)}"
    return ep_id, 0.6, f"ambiguous_endpoint url={url} candidates={len(pool)}"


def link_api_calls(
    frontend_api_nodes: list[GraphNode],
    backend_endpoint_nodes: list[GraphNode],
) -> list[GraphEdge]:
    """匹配 frontend_api_call -> backend_endpoint, 建 calls_api 边(单一真值源, 任何栈组合都调本函数)。

    每个前端节点: operationId 精确桥优先, 未命中退 URL 桥(多服务消歧)。两者皆不中 → 不建边
    (不假连, 守 impact 确定 vs 候选拆分)。
    """
    by_op = _index_by_operation(backend_endpoint_nodes)
    by_url = _index_by_url(backend_endpoint_nodes)
    edges: list[GraphEdge] = []
    for fn in frontend_api_nodes:
        chosen = _resolve_by_operation(fn, by_op) or _resolve_by_url(fn, by_url)
        if chosen is None:
            continue
        ep_id, conf, evidence = chosen
        edges.append(
            GraphEdge(
                source=fn.id,
                target=ep_id,
                kind=EdgeKind.CALLS_API.value,
                confidence=conf,
                meta={"evidence": evidence},
            )
        )
    return edges
