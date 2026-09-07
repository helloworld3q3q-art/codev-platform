"""跨 lane 检索融合核心 (Phase 6 MVP 切片) —— 加权 RRF + 候选级 boost + 来源可解释。

把各检索 lane(vector docs / bm25 / codegraph 符号 / graph 邻域 / memory)产出的**已排序
候选**合成一路统一排名。本模块只做**融合数学**(加权 RRF + boost + 合并), 不碰任何 lane 的
取数 —— lane 适配(调 chroma/codegraph/graph)、query 分类选权重(复用 Phase 7 planner)都在
调用侧。纯函数、确定性、无 IO、可脱离 daemon 单测。

与 `agent/recall.RrfFusion` 同源(RRF 公式一致 `Σ 1/(k+rank+1)`), 但那是 memory 单类型召回、
不加权; 本模块跨**异构 lane**、加权、且结果**可解释**(每候选标出命中 lane + 分数), 满足
plan Phase 6 Gate「召回能解释来自哪个 lane、为什么排前」「reranker 关闭仍稳定(加权 RRF 即兜底)」。

扩展点(不改核心):
- 加 lane = 多传一个 LaneResult(+ 在 weights 给它一个权重)。
- 调权重 = 改 weights 数据(query 分类驱动), 不是改代码分支。
- boost(exact symbol / freshness / community)由调用侧算好按 ref 喂进来, 核心不耦合"何为精确符号"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# RRF 常数: 论文(Cormack 2009)默认 60, 与 chroma.bm25 / agent.recall 全平台一致。
_RRF_K = 60


@dataclass
class LaneResult:
    """一个检索 lane 的产出: 该 lane 名 + 已按相关性排好序的候选 ref(best-first)。

    lane:   lane 标识(vector_docs / bm25_docs / codegraph / graph / memory ...)。
    ranked: 候选 ref 列表, 最相关在前。ref 是跨 lane 唯一标识(chunk_id / node_id / symbol ...);
            同一对象被多 lane 命中须用**同一 ref** 才能在融合时叠加得分。
    """

    lane: str
    ranked: list[str] = field(default_factory=list)


@dataclass
class FusedHit:
    """一条融合结果 —— 排名 + 可解释来源。

    ref:    候选标识。
    score:  融合总分(加权 RRF + boost)。
    lanes:  命中它的 lane 名(按权重降序)—— 解释"来自哪些 lane"。
    boost:  额外提升量(exact symbol / freshness 等, 调用侧喂入)—— 解释"为什么排前"。
    """

    ref: str
    score: float
    lanes: list[str] = field(default_factory=list)
    boost: float = 0.0


def weighted_rrf(
    lanes: list[LaneResult],
    *,
    weights: dict[str, float] | None = None,
    k: int = _RRF_K,
    boosts: dict[str, float] | None = None,
    top_k_per_lane: int | None = None,
    limit: int | None = None,
) -> list[FusedHit]:
    """加权 RRF 融合多 lane 候选 → 统一降序排名(稳定: 同分保首见序)。

    score(ref) = Σ_lane  weight[lane] · 1/(k + rank_in_lane + 1)   +  boost[ref]

    weights:        lane → 权重(缺省该 lane 取 1.0; 不传 weights = 全 1.0 等权)。
                    权重是**数据**(由 query 分类 / planner 给), 核心不内置 lane 偏好。
    boosts:         ref → 额外加分(exact symbol / freshness / community), 调用侧算好喂入。
    top_k_per_lane: 每 lane 融合前只取前 N 个候选(plan「lane-specific top_k」); None=全取。
    limit:          最终结果上限; None=全返回。
    空 lane / 空候选优雅跳过; 单 lane 退化为该 lane 顺序(乘权重不改序)。
    """
    w = weights or {}
    boost_map = boosts or {}
    scores: dict[str, float] = {}
    hit_lanes: dict[str, list[tuple[float, str]]] = {}   # ref → [(weight, lane)] 供解释排序
    order: list[str] = []                                 # 首见序 → 稳定 tie-break

    for lr in lanes:
        if not lr.ranked:
            continue
        lane_w = w.get(lr.lane, 1.0)
        ranked = lr.ranked[:top_k_per_lane] if top_k_per_lane else lr.ranked
        for rank, ref in enumerate(ranked):
            if ref not in scores:
                order.append(ref)
                scores[ref] = 0.0
                hit_lanes[ref] = []
            scores[ref] += lane_w * (1.0 / (k + rank + 1))
            hit_lanes[ref].append((lane_w, lr.lane))

    # boost 叠加(独立于 lane 命中: 即便某 ref 只在 boost_map 里也不凭空入选 —— 只提升已召回项)。
    for ref in order:
        b = boost_map.get(ref)
        if b:
            scores[ref] += b

    order.sort(key=lambda r: -scores[r])   # 稳定排序: 同分保首见序
    hits = [
        FusedHit(
            ref=r, score=scores[r],
            lanes=[lane for _, lane in sorted(hit_lanes[r], key=lambda x: -x[0])],
            boost=boost_map.get(r, 0.0),
        )
        for r in order
    ]
    return hits[:limit] if limit else hits
