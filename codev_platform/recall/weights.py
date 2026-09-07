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
from codev_platform.recall.service import CODEGRAPH_LANE, GRAPH_LANE, VECTOR_LANE

_PREFER = 2.0   # topic lane 偏好倍数(prefer-not-exclude; 由 eval 调, 非定死)
_BASE = 1.0

# query 类型 → 各 lane 权重(数据驱动)。未列 lane 由 weighted_rrf 缺省 1.0。
# **vector lane 一律 _BASE(均衡), 不给偏好**: 2026-06-11 eval 实测「概览/兜底偏好 vector 2x」
# 比三 lane 等权**显著更差**(codev-platform nDCG@5 delta CI95 [-0.262,-0.063] 全负)—— 等权
# 即最优(MRR 0.917)。vector lane 本身价值巨大(等权 3-lane vs 2-lane: MRR +0.40 / nDCG +0.35,
# CI 全正), 但**不该再加偏好**(过偏单 lane 反伤融合)。符号 / 影响类的 codegraph / graph 偏好
# 仍保留(精确符号 FTS / 架构血缘强), vector 在各类只作等权参与。
_WEIGHTS_BY_TYPE: dict[str, dict[str, float]] = {
    QueryType.SYMBOL:   {CODEGRAPH_LANE: _PREFER, GRAPH_LANE: _BASE, VECTOR_LANE: _BASE},   # 符号: 谁定义/调用 → codegraph
    QueryType.IMPACT:   {GRAPH_LANE: _PREFER, CODEGRAPH_LANE: _BASE, VECTOR_LANE: _BASE},   # 影响/跨层 → graph
    QueryType.OVERVIEW: {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE, VECTOR_LANE: _BASE},     # 概览 → 均衡(含 vector)
    QueryType.DOC_RULE: {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE, VECTOR_LANE: _BASE},     # 文档(本组无 doc lane)→ 均衡
    QueryType.GENERAL:  {GRAPH_LANE: _BASE, CODEGRAPH_LANE: _BASE, VECTOR_LANE: _BASE},     # 兜底 → 均衡(含 vector)
}


def lane_weights_for(query: str) -> dict[str, float]:
    """按 query 类型给融合 lane 权重(确定性, 复用 planner.classify_query, 不调 LLM)。"""
    return dict(_WEIGHTS_BY_TYPE.get(classify_query(query), {}))
