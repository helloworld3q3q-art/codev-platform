"""跨 lane 代码召回服务 (Phase 6) —— 融合 graph + codegraph + vector(语义)三 lane。

把 agent 现在要分别调的 `graph search_nodes`(架构/跨层节点)+ `codegraph search`(符号 FTS)
+ **vector 语义召回**(对 codegraph 节点嵌入, 补「按行为描述找代码」盲区)合成**一路带来源
解释的统一排名**: query 命中多 lane 的对象得分叠加上浮, 每条结果标出来自哪些 lane。query
类型驱动的权重(symbol 偏 codegraph / 架构偏 graph; vector 均衡)由调用侧 / planner 传入。

graph + codegraph 两 lane 直读 per-project 只读 sqlite(**不经 daemon**), vector lane 走 chroma
向量库 + 嵌入(缺则降级)。三 lane 全 **fail-soft**: 某 lane(如 codegraph db / 向量索引未建)
失败仅记日志, 其余 lane 仍出结果(高可用; 对齐 plan 'reranker 关闭仍稳定')。融合 + 富化纯函数
可脱 IO 单测。vector lane 实证大幅加分(2026-06-11: 等权 3-lane vs 2-lane MRR +0.40~0.51, CI 全正)。

融合数学在 recall/fusion(纯核心); 本文件只做"取数 → 建 LaneResult → 融合 → 富化"的编排,
其中**融合 + 富化(_fuse_and_enrich)是纯函数**(脱 IO 单测), IO 取数是薄壳。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from codev_platform.recall.fusion import LaneResult, weighted_rrf

logger = logging.getLogger(__name__)

GRAPH_LANE = "graph"
CODEGRAPH_LANE = "codegraph"
VECTOR_LANE = "vector"
_PER_LANE = 30   # 每 lane 融合前取前 N(plan 'lane-specific top_k')
_LIMIT = 20      # 最终返回上限
_LOGGED_LANE_FAILURES: set[tuple[str, str, str, str]] = set()


def _log_lane_failure(lane: str, project_id: str, exc: Exception) -> None:
    """Warn once per stable lane failure; repeated optional-lane misses stay debug-only."""
    key = (lane, project_id, type(exc).__name__, str(exc))
    if key in _LOGGED_LANE_FAILURES:
        logger.debug("[recall] %s lane still unavailable: %r", lane, exc)
        return
    _LOGGED_LANE_FAILURES.add(key)
    logger.warning("[recall] %s lane failed: %r", lane, exc)


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


_TEST_FILE_HINTS = ("/tests/", "/test/")


def _is_test_hit(name: str | None, file: str | None) -> bool:
    """该 hit 是测试代码(test_ 函数 / tests 目录 / *_test.py / *.spec.ts)?

    recall 默认要**实现**, 同名测试函数(如 test_stamp_provenance_xxx)不该压过真符号
    (stamp_provenance)。bm25/相关性排序不区分这点, 故 lane 内显式降权。
    """
    n = (name or "").lower()
    f = (file or "").lower()
    return (n.startswith("test_") or f.startswith("tests/")
            or any(h in f for h in _TEST_FILE_HINTS)
            or f.endswith("_test.py") or f.endswith(".test.ts") or f.endswith(".spec.ts"))


def _deprioritize_tests(refs_meta: list[tuple[str, str | None, str | None]]) -> list[str]:
    """[(ref, name, file)] 原序(bm25/相关性)→ **非测试在前、测试靠后**, 组内保原序 → ref 序。"""
    impl = [ref for ref, n, f in refs_meta if not _is_test_hit(n, f)]
    tests = [ref for ref, n, f in refs_meta if _is_test_hit(n, f)]
    return impl + tests


def _merge_ranked_groups(groups: list[list[str]]) -> list[str]:
    """多个 repo 的 lane 内排序 → round-robin 合并, 组内顺序不变。

    不把 extra repo 整组压在主仓所有结果之后; 同时不引入跨 DB 打分归一化复杂度。
    """
    out: list[str] = []
    seen: set[str] = set()
    max_len = max((len(g) for g in groups), default=0)
    for i in range(max_len):
        for g in groups:
            if i < len(g) and g[i] not in seen:
                seen.add(g[i])
                out.append(g[i])
    return out


def _graph_lane(project_id: str, query: str, per_lane: int) -> tuple[LaneResult | None, dict]:
    """graph 邻域 lane: search_nodes 直读 graph store(免 daemon)。失败/空 → (None, {})。"""
    try:
        from codev_platform.graph.impact import search_nodes
        from codev_platform.graph.store import open_store
        # 读 lane 走 mode='ro': 不创建/不迁移本地存储(缺/旧 store → GraphStoreUnreadable, 下面 except 兜)。
        with open_store(project_id, mode="ro") as store:
            res = search_nodes(store, project_id, query, limit=per_lane)
    except Exception as exc:  # noqa: BLE001 — 单 lane 失败不拖垮整体(高可用)
        _log_lane_failure(GRAPH_LANE, project_id, exc)
        return None, {}
    hits = res.get("hits", [])
    details = {h["id"]: {"name": h.get("name"), "kind": h.get("kind"), "file": h.get("file")}
               for h in hits}
    ranked = _deprioritize_tests([(h["id"], h.get("name"), h.get("file")) for h in hits])
    return LaneResult(GRAPH_LANE, ranked), details


def _codegraph_lane(project_id: str, query: str, per_lane: int) -> tuple[LaneResult | None, dict]:
    """codegraph 符号 lane: FTS search 直读 codegraph.db(免 daemon)。失败/空 → (None, {})。"""
    try:
        from codev_platform.core.repos import project_repo_specs
        from codev_platform.web.integrations.codegraph_client import CodegraphClient
    except Exception as exc:  # noqa: BLE001 — codegraph db 未建等 → 跳过该 lane
        _log_lane_failure(CODEGRAPH_LANE, project_id, exc)
        return None, {}
    details: dict = {}
    groups: list[list[str]] = []
    specs = project_repo_specs(project_id)
    if not specs:
        try:
            with CodegraphClient(project_id=project_id) as cg:
                rows = cg.search(query, None, None, per_lane, match_mode="or")
        except Exception as exc:  # noqa: BLE001 — legacy 集中库也不可用 → 跳过该 lane
            _log_lane_failure(CODEGRAPH_LANE, project_id, exc)
            return None, {}
        refs_meta = []
        for r in rows:
            ref = r["id"]
            file = r.get("filePath")
            details[ref] = {"name": r.get("name"), "kind": r.get("kind"), "file": file}
            refs_meta.append((ref, r.get("name"), file))
        ranked = _deprioritize_tests(refs_meta)
        return (LaneResult(CODEGRAPH_LANE, ranked), details) if ranked else (None, {})
    for spec in specs:
        try:
            with CodegraphClient(db_path=spec.codegraph_db) as cg:
                # match_mode='or': verbose 多词 query(混入 function/definition 等描述词)AND 会
                # 全灭, OR 让目标符号被 bm25 顶上来(与 graph lane 分词宽松召回同理)。
                rows = cg.search(query, None, None, per_lane, match_mode="or")
        except Exception as exc:  # noqa: BLE001 — 单 repo 失败不拖垮其它 repo
            repo_label = "main repo" if spec.is_main else "extra repo"
            logger.warning("[recall] codegraph %s skipped (%s): %r", repo_label, spec.root, exc)
            continue
        refs_meta: list[tuple[str, str | None, str | None]] = []
        for r in rows:
            ref = spec.local_ref(r["id"])
            file = spec.local_file(r.get("filePath"))
            details[ref] = {"name": r.get("name"), "kind": r.get("kind"), "file": file}
            refs_meta.append((ref, r.get("name"), file))
        ranked_group = _deprioritize_tests(refs_meta)
        if ranked_group:
            groups.append(ranked_group)
    ranked = _merge_ranked_groups(groups)
    if not ranked:
        return None, {}
    return LaneResult(CODEGRAPH_LANE, ranked), details


def _vector_lane(project_id: str, query: str, per_lane: int) -> tuple[LaneResult | None, dict]:
    """vector 语义 lane: 对 codegraph 节点嵌入做相似度召回(免 daemon, 复用平台 Embedder)。

    ref 与 codegraph lane **同空间**(codegraph node id)→ 融合时同一对象叠分。嵌入模型 / collection
    缺(未建索引 / 无 sentence-transformers / chromadb)→ (None, {}), 与其它 lane 同 fail-soft。
    """
    try:
        from codev_platform.recall.code_vector_store import query_code_vectors
        ranked_ids, details = query_code_vectors(project_id, query, per_lane)
    except Exception as exc:  # noqa: BLE001 — collection 未建 / 依赖缺 → 跳过该 lane(高可用)
        _log_lane_failure(VECTOR_LANE, project_id, exc)
        return None, {}
    if not ranked_ids:
        return None, {}
    ranked = _deprioritize_tests(
        [(nid, details.get(nid, {}).get("name"), details.get(nid, {}).get("file")) for nid in ranked_ids])
    return LaneResult(VECTOR_LANE, ranked), details


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
    from codev_platform.recall.observability import RecallTrace
    trace = RecallTrace(query, project_id)   # Phase 8: per-lane 计时/候选/无证据(best-effort)
    lanes: list[LaneResult] = []
    details: dict[str, dict] = {}
    # lane 列表**就地**列(引用模块级函数, 调用期才解析名字 → 测试 monkeypatch 这些函数有效)。
    # 勿提成模块级常量: 那会在 import 期 capture 死函数引用, 绕过 monkeypatch(捕获引用 footgun)。
    for name, lane_fn in (("graph", _graph_lane), ("codegraph", _codegraph_lane),
                          ("vector", _vector_lane)):
        t0 = time.monotonic()
        lane, lane_details = lane_fn(project_id, query, per_lane)
        cand = len(lane.ranked) if (lane is not None and lane.ranked) else 0
        trace.add_lane(name, (time.monotonic() - t0) * 1000.0, cand)
        if lane is not None and lane.ranked:
            lanes.append(lane)
            for ref, d in lane_details.items():
                details.setdefault(ref, d)   # 同 ref 多 lane: 首见富化(graph 先)
    hits = _fuse_and_enrich(lanes, details, weights=weights, limit=limit)
    trace.finish(len(hits))   # 落 trace(无证据 = 融合空); 失败静默不阻断
    # 精排(Phase 6 reranker): config `recall.rerank.enabled` 开则 cross-encoder 重排 top 候选,
    # 关 / 不可用 / 失败 → 原序(加权 RRF), 绝不因精排丢结果(plan Gate「reranker 关仍稳定」)。
    from codev_platform.recall.rerank import maybe_rerank_hits
    return maybe_rerank_hits(query, project_id, hits)
