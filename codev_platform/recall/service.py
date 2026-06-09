"""跨 lane 代码召回服务 (Phase 6 MVP) —— 融合 graph + codegraph 两条**本地 sqlite** lane。

把 agent 现在要分别调的 `graph search_nodes`(架构/跨层节点)+ `codegraph search`(符号 FTS)
合成**一路带来源解释的统一排名**: query 命中两 lane 的对象得分叠加上浮, 每条结果标出来自
哪些 lane。query 类型驱动的权重(symbol 偏 codegraph / 架构偏 graph)由调用侧 / planner 传入。

两条 lane 都直读 per-project 只读 sqlite(**不经 daemon**, 与 web routes/graph 同), 故本服务
可脱 daemon 跑 + 单测。**每 lane fail-soft**: 某 lane(如 codegraph db 未建)失败仅记日志,
另一 lane 仍出结果(高可用; 对齐 plan 'reranker 关闭仍稳定')。

融合数学在 recall/fusion(纯核心); 本文件只做"取数 → 建 LaneResult → 融合 → 富化"的编排,
其中**融合 + 富化(_fuse_and_enrich)是纯函数**(脱 IO 单测), IO 取数是薄壳。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from codev_platform.recall.fusion import LaneResult, weighted_rrf

logger = logging.getLogger(__name__)

GRAPH_LANE = "graph"
CODEGRAPH_LANE = "codegraph"
_PER_LANE = 30   # 每 lane 融合前取前 N(plan 'lane-specific top_k')
_LIMIT = 20      # 最终返回上限


@dataclass
class CodeRecallHit:
    """一条融合后的代码召回结果 —— 排名 + 富化元信息 + 可解释来源。"""

    ref: str
    score: float
    name: str
    kind: str
    file: str | None = None
    lanes: list[str] = field(default_factory=list)   # 命中它的 lane(可解释)


def _fuse_and_enrich(lanes: list[LaneResult], details: dict[str, dict], *,
                     weights: dict[str, float] | None = None,
                     limit: int = _LIMIT) -> list[CodeRecallHit]:
    """纯函数: 融合多 lane + 用 details 富化为 CodeRecallHit。脱 IO, 可单测。

    details: ref → {name, kind, file}(各 lane 取数时收集; 同 ref 多 lane 命中取首见)。
    """
    fused = weighted_rrf(lanes, weights=weights, top_k_per_lane=_PER_LANE, limit=limit)
    out: list[CodeRecallHit] = []
    for h in fused:
        d = details.get(h.ref, {})
        out.append(CodeRecallHit(
            ref=h.ref, score=h.score, lanes=h.lanes,
            name=d.get("name", h.ref), kind=d.get("kind", ""), file=d.get("file"),
        ))
    return out


def _graph_lane(project_id: str, query: str, per_lane: int) -> tuple[LaneResult | None, dict]:
    """graph 邻域 lane: search_nodes 直读 graph store(免 daemon)。失败/空 → (None, {})。"""
    try:
        from codev_platform.graph.impact import search_nodes
        from codev_platform.graph.store import open_store
        conn = open_store(project_id)
        try:
            res = search_nodes(conn, project_id, query, limit=per_lane)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — 单 lane 失败不拖垮整体(高可用)
        logger.warning("[recall] graph lane failed: %r", exc)
        return None, {}
    hits = res.get("hits", [])
    details = {h["id"]: {"name": h.get("name"), "kind": h.get("kind"), "file": h.get("file")}
               for h in hits}
    return LaneResult(GRAPH_LANE, [h["id"] for h in hits]), details


def _codegraph_lane(project_id: str, query: str, per_lane: int) -> tuple[LaneResult | None, dict]:
    """codegraph 符号 lane: FTS search 直读 codegraph.db(免 daemon)。失败/空 → (None, {})。"""
    try:
        from codev_platform.web.integrations.codegraph_client import CodegraphClient
        with CodegraphClient(project_id) as cg:
            rows = cg.search(query, None, None, per_lane)
    except Exception as exc:  # noqa: BLE001 — codegraph db 未建等 → 跳过该 lane
        logger.warning("[recall] codegraph lane failed: %r", exc)
        return None, {}
    details = {r["id"]: {"name": r.get("name"), "kind": r.get("kind"), "file": r.get("filePath")}
               for r in rows}
    return LaneResult(CODEGRAPH_LANE, [r["id"] for r in rows]), details


def recall_code(query: str, project_id: str, *,
                weights: dict[str, float] | None = None,
                limit: int = _LIMIT, per_lane: int = _PER_LANE) -> list[CodeRecallHit]:
    """跨 lane 代码召回: 融合 graph + codegraph → 统一可解释排名。

    weights: lane → 权重。**缺省(None)按 query 类型自动调权**(planner 分类 → symbol 偏
             codegraph / 架构类偏 graph), 见 recall.weights; 显式传入则覆盖自动值。
    每 lane fail-soft: 一条挂了另一条仍出结果。两 lane 全空 → []。
    """
    if not (query or "").strip():
        return []
    if weights is None:   # 自动按 query 类型调权(planner 耦合隔离在 recall.weights)
        from codev_platform.recall.weights import lane_weights_for
        weights = lane_weights_for(query)
    lanes: list[LaneResult] = []
    details: dict[str, dict] = {}
    for lane, lane_details in (_graph_lane(project_id, query, per_lane),
                               _codegraph_lane(project_id, query, per_lane)):
        if lane is not None and lane.ranked:
            lanes.append(lane)
            for ref, d in lane_details.items():
                details.setdefault(ref, d)   # 同 ref 多 lane: 首见富化(graph 先)
    return _fuse_and_enrich(lanes, details, weights=weights, limit=limit)
