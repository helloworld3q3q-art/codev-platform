"""调用边 resolver 共享层: 方法名 BFS + endpoint→碰表函数连边。

各语言 resolver 的差异**只在**「怎么把源码解析成 calls_by_func(函数名 -> 被调方法名集合)」:
Python 用 ast, Java/C#/JS 用正则。一旦有了 calls_by_func, 从 handler 逐层下钻、命中碰表
backend_function 即连 endpoint→function 边的逻辑**完全一致**, 抽到此处复用 —— 加语言栈只需
写解析层 + 调 build_call_edges, 不重复 BFS / 连边 / 去重(协议族铁律, 同 plugins registry)。
"""
from __future__ import annotations

from codev_platform.graph.schema import EdgeKind, GraphEdge, GraphNode

_DEFAULT_MAX_VISIT = 500  # 单 handler 可达方法名上限(防调用图爆炸)


def reachable_names(
    start: str, calls_by_func: dict[str, set[str]], *, max_depth: int,
    max_visit: int = _DEFAULT_MAX_VISIT,
) -> set[str]:
    """从 start 函数名 BFS, 返回深度 / 访问受限内可达的所有被调方法名(含中间 service 方法名)。"""
    out: set[str] = set()
    frontier = {start}
    depth = 0
    while frontier and depth < max_depth and len(out) < max_visit:
        nxt: set[str] = set()
        for name in frontier:
            for callee in calls_by_func.get(name, ()):
                if callee not in out:
                    out.add(callee)
                    nxt.add(callee)
        frontier = nxt
        depth += 1
    return out


def build_call_edges(
    endpoints: list[GraphNode], tablefns: list[GraphNode],
    calls_by_func: dict[str, set[str]], *, resolver: str, confidence: float,
    max_depth: int, max_visit: int = _DEFAULT_MAX_VISIT,
) -> list[GraphEdge]:
    """每个 endpoint handler BFS, 命中碰表函数连 endpoint→function CALLS 边(同 (ep,fn) 去重)。

    endpoints / tablefns: 调用方按语言已过滤好的节点。calls_by_func: 语言解析层产物(已应用
    各自的关键字 / 通用名黑名单)。confidence 由各 resolver 定(语义见各自 docstring + base)。
    """
    fn_by_name: dict[str, list[GraphNode]] = {}
    for fn in tablefns:
        fn_by_name.setdefault(fn.name, []).append(fn)

    edges: list[GraphEdge] = []
    seen: set[tuple[str, str]] = set()
    for ep in endpoints:
        handler = (ep.meta or {}).get("handler") or ep.name
        for target in reachable_names(handler, calls_by_func,
                                      max_depth=max_depth, max_visit=max_visit):
            for fn in fn_by_name.get(target, ()):
                key = (ep.id, fn.id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(GraphEdge(
                    source=ep.id, target=fn.id,
                    kind=EdgeKind.CALLS.value, confidence=confidence,
                    meta={"resolver": resolver, "via_handler": handler},
                ))
    return edges
