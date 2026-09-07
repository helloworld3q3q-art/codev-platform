"""影响图边的冲突消解 (Phase 3) —— 从 impact.py 拆出 (file-discipline §1 600 行预算)。

`build_impact_graph` 构图前调用: 同 (source,target,kind) 多边(多 plugin/parser 给同一关系,
或退役 plugin 残留)→ 只保留 provenance 更全、置信更高者。保首见序, 无冲突原样返回。
"""
from __future__ import annotations

from codev_platform.graph.schema import GraphEdge, edge_provenance


def resolve_duplicate_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    """冲突消解 (Phase 3): 同 (source,target,kind) 多边 → 保留 **provenance 更全、置信更高**
    者(stamped 胜 unstamped)。保首见序, 无冲突原样返回。

    实测 openclaw: 155 组重复(退役 builtin.codegraph_bridge 残留 vs call_resolvers 同边, 一个
    盖戳一个没盖)→ 去重让影响图邻接不膨胀 + 路径 provenance 一致。
    """
    def _rank(e: GraphEdge) -> tuple[int, float]:
        return (1 if edge_provenance(e.meta) else 0,
                e.confidence if e.confidence is not None else 1.0)

    best: dict[tuple[str, str, str], GraphEdge] = {}
    order: list[tuple[str, str, str]] = []
    for e in edges:
        key = (e.source, e.target, e.kind)
        cur = best.get(key)
        if cur is None:
            best[key] = e
            order.append(key)
        elif _rank(e) > _rank(cur):
            best[key] = e
    return [best[k] for k in order]
