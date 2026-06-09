"""query 类型 → 融合 lane 权重 (Phase 6) —— 复用 planner.classify_query, 数据表驱动。

让 recall_code 按问题类型**自动调权**: symbol 链路偏 codegraph(符号 FTS 强)、impact/跨层偏
graph(架构血缘强)、概览/文档/兜底均衡。分类**确定性**(planner 关键词打分, 不调 LLM),
对不对可由 eval `planner` suite 回归。

设计:
- 偏好倍数 `_PREFER` 是**透明起点**(prefer-not-exclude, 非'已证最优'常数); 由 Phase 0 eval
  (MRR/nDCG)调。调偏好 = 改 `_WEIGHTS_BY_TYPE` 这张**数据表**, 不改代码分支。
- recall → planner 的耦合**只在本文件**; fusion(纯核心)/ service(lane 编排)都不依赖 planner。
- 加 lane / 加 query 类型 = 改数据表一行, 核心零改。
"""
from __future__ import annotations

from codev_platform.agent.planner import QueryType, classify_query
from codev_platform.recall.service import CODEGRAPH_LANE, GRAPH_LANE

_PREFER = 2.0   # topic lane 偏好倍数(prefer-not-exclude; 由 eval 调, 非定死)
_BASE = 1.0

# query 类型 → 各 lane 权重(数据驱动)。只列当前两 lane; 未列 lane 由 weighted_rrf 缺省 1.0。
_WEIGHTS_BY_TYPE: dict[str, dict[str, float]] = {
    QueryType.SYMBOL:   {CODEGRAPH_LANE: _PREFER, GRAPH_LANE: _BASE},   # 符号: 谁定义/调用 → codegraph
    QueryType.IMPACT:   {GRAPH_LANE: _PREFER, CODEGRAPH_LANE: _BASE},   # 影响/跨层 → graph
    QueryType.OVERVIEW: {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE},     # 概览 → 均衡
    QueryType.DOC_RULE: {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE},     # 文档(本 2 lane 无 doc lane)→ 均衡
    QueryType.GENERAL:  {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE},     # 兜底 → 均衡
}


def lane_weights_for(query: str) -> dict[str, float]:
    """按 query 类型给融合 lane 权重(确定性, 复用 planner.classify_query, 不调 LLM)。"""
    return dict(_WEIGHTS_BY_TYPE.get(classify_query(query), {}))
