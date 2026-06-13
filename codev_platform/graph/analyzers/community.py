"""结构社区 analyzer(Phase 4)—— 把 community.py 的算法输出翻译成统一图谱软层。

实现 A1-1 的 Analyzer 协议(与 business_domain / arch_layer 同构, 同走 _analyzers_pass)。
**唯一职责 = 适配**: 过滤软产物 → 调 detect_communities(算法纯核)→ 产 COMMUNITY 软节点 +
IN_COMMUNITY 软边。无算法逻辑(算法在 community.py)。

与 LLM analyzer 的区别: 确定性 + 免费 → **无条件注册(默认开)**, 不进 LLM 的 config gate。
软隔离(COMMUNITY/IN_COMMUNITY ∈ SOFT_*_KINDS)→ impact 查依赖默认过滤, 不污染血缘。

只产 size ≥ _MIN_SIZE 的社区(孤立/二元噪声不产 → "和谁抱团"才有意义); 社区软节点 id =
`<pid>:community:c<rank>`(rank 按社区代表 min-id 排序, 确定性可复现)。
"""
from __future__ import annotations

from collections import Counter

from codev_platform.graph.community import detect_communities
from codev_platform.graph.schema import (
    AnalyzerResult,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    is_soft_edge_kind,
    is_soft_node_kind,
)

_CONF = 0.7              # 软产物置信(validate_soft_result 也会兜底钳 < 1.0)
_MIN_NODES = 3          # applies 门槛: 太小的图不值得社区检测
_MIN_SIZE = 2           # 只产成员数 ≥ 此的社区(单点社区无"抱团"语义, 不产)
_SAMPLE = 5            # meta 里存几个代表成员名(给 overview 可读)


class CommunityAnalyzer:
    """结构社区 analyzer。算法经 community.detect_communities 注入(本类不含算法逻辑)。"""

    name = "community"

    def __init__(self, *, min_size: int = _MIN_SIZE) -> None:
        self._min_size = min_size

    def applies(self, nodes: list[GraphNode]) -> bool:
        hard = sum(1 for n in nodes if not is_soft_node_kind(n.kind))
        return hard >= _MIN_NODES

    def analyze(self, project_id: str, nodes: list[GraphNode],
                edges: list[GraphEdge]) -> AnalyzerResult:
        hard_nodes = [n for n in nodes if not is_soft_node_kind(n.kind)]
        hard_edges = [e for e in edges if not is_soft_edge_kind(e.kind)]
        mapping = detect_communities(hard_nodes, hard_edges)
        if not mapping:
            return AnalyzerResult(plugin=self.name)

        by_id = {n.id: n for n in hard_nodes}
        groups: dict[str, list[str]] = {}
        for nid, cid in mapping.items():
            groups.setdefault(cid, []).append(nid)
        # 只留 size ≥ min_size 的社区; 按社区代表(成员最小 id)排序定 rank(确定性)。
        big = sorted(c for c, mem in groups.items() if len(mem) >= self._min_size)

        soft_nodes: list[GraphNode] = []
        soft_edges: list[GraphEdge] = []
        for rank, cid in enumerate(big):
            members = sorted(groups[cid])
            comm_id = f"{project_id}:community:c{rank}"
            kinds = Counter(by_id[m].kind for m in members if m in by_id)
            dominant = kinds.most_common(1)[0][0] if kinds else "mixed"
            sample = [by_id[m].name for m in members[:_SAMPLE] if m in by_id]
            soft_nodes.append(GraphNode(
                id=comm_id, kind=NodeKind.COMMUNITY.value, name=f"community-{rank}",
                project_id=project_id,
                meta={"derived_by": self.name, "confidence": _CONF,
                      "size": len(members), "dominant_kind": dominant, "sample": sample},
            ))
            for nid in members:
                soft_edges.append(GraphEdge(
                    source=nid, target=comm_id,
                    kind=EdgeKind.IN_COMMUNITY.value, confidence=_CONF))
        return AnalyzerResult(nodes=soft_nodes, edges=soft_edges, plugin=self.name)
