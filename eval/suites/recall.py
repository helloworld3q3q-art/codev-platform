"""recall suite —— 跨 lane 融合召回 A/B 验 planner 权重 (Phase 6)。

加权(planner 自动)vs 等权, 比 MRR / nDCG@k + delta。落地 Phase 6 Gate「相比 baseline 可
量化提升」+ 验 `_PREFER` 是否真有效。两 lane(graph + codegraph)直读本地 sqlite(免 daemon);
任一 store 缺 → skip(单 lane 下加权无意义, 比较不成立)。

⚠️ **本 suite 不测 vector lane 的净价值**(评估盲区, 非 prod bug): 两臂(weighted/uniform)都经
recall_code 跑全部三 lane(含 vector), general 行三 lane 权重相等 → vector 对 paired delta 结构性
不敏感, 关掉 vector 两臂等额变化、delta 仍 ≈0, 本 suite 测不到。**vector lane 价值由独立
3-lane-vs-2-lane 消融实验佐证**(见 MEMORY [[phase6-vector-lane]]: codev MRR +0.43 / openclaw +0.57,
CI 全正), 不在此 suite 重复。注: 不能用 weights={VECTOR:0} 做消融 —— weighted_rrf 对 weight=0 的
lane 仍把候选 ref 计入排名(只是 0 贡献), 不会真正剔除该 lane, 故 weights 消融会给误导性近零 delta;
真消融需 service 层跳过该 LaneResult(另一机制, 当前不做)。
"""
from __future__ import annotations

from eval.metrics import aggregate_mrr, aggregate_ndcg, mrr, ndcg_at_k
from eval.suites._common import _bootstrap_ci, load_jsonl, token_match

_RECALL_RELEVANT_FIELDS = ("name", "file")


def _recall_per_query(hits: list, expect: str) -> tuple[list[str], set[str], int]:
    """recall_code 结果 → (位置 id 列表, 相关位置集合, 首个相关 1-based rank)。

    相关性按 **token 边界匹配**(hit 的 name/file 把 expect 作独立 token 含)—— 2026-06-10
    专家面板指出: 原裸子串 `expect in name` 假阳(`"impact"` 命中任何含 impact 的节点 → relevant
    虚大 → rank 虚高), 与 agent_e2e 已修的 `_mentions` 不一致。改用共享 `token_match`(单一真值源)。

    **只标真实现 ref**(audit #1): 测试代码(test_ 函数 / tests 目录 / *_test.py / *.spec.ts)即使
    name/file 含 expect 也**不算相关** —— 复用 service 的 `_is_test_hit`(与 lane 内降权同一定义)。
    """
    from codev_platform.recall.service import _is_test_hit

    retrieved = [str(i) for i in range(len(hits))]
    relevant = {
        str(i) for i, h in enumerate(hits)
        if any(token_match(getattr(h, f, None), expect) for f in _RECALL_RELEVANT_FIELDS)
        and not _is_test_hit(getattr(h, "name", None), getattr(h, "file", None))
    }
    rank = next((i + 1 for i in range(len(hits)) if str(i) in relevant), -1)
    return retrieved, relevant, rank


def run_recall(project_id: str, k: int = 5) -> dict:
    from codev_platform.core.repos import project_codegraph_dbs
    from codev_platform.graph.store import graph_store_path
    from codev_platform.recall import recall_code
    from codev_platform.recall.service import CODEGRAPH_LANE, GRAPH_LANE

    # 按 project_id 过滤金标行(无字段的旧行默认归 codev-platform)—— recall A/B 是 per-project
    # (加权 vs 等权在该项目自己的双 lane 内比), 故按 --project 选该项目的用例, 不混跑别项目。
    rows = [r for r in load_jsonl("recall.jsonl")
            if r.get("project_id", "codev-platform") == project_id]
    if not rows:
        return {"suite": "recall", "status": "skipped", "n": 0,
                "reason": f"无 project={project_id} 的 recall 用例(检查 recall.jsonl 的 project_id)。"}
    if not graph_store_path(project_id).exists() or not project_codegraph_dbs(project_id):
        return {
            "suite": "recall", "status": "skipped", "n": len(rows),
            "reason": f"需 graph store + codegraph.db 双 lane (project={project_id}); "
                      f"缺一则单 lane, 加权 vs 等权比较不成立。",
        }
    uniform = {GRAPH_LANE: 1.0, CODEGRAPH_LANE: 1.0}
    weighted_pq: list[tuple[list[str], set[str]]] = []
    uniform_pq: list[tuple[list[str], set[str]]] = []
    rr_deltas: list[float] = []      # per-query reciprocal-rank 差 (weighted - uniform)
    ndcg_deltas: list[float] = []    # per-query nDCG@k 差 (weighted - uniform)
    details = []
    for r in rows:
        expect = r["expect"]
        w_hits = recall_code(r["query"], project_id, weights=None, limit=max(k, 10))   # planner 自动
        u_hits = recall_code(r["query"], project_id, weights=uniform, limit=max(k, 10))
        wr, wrel, w_rank = _recall_per_query(w_hits, expect)
        ur, urel, u_rank = _recall_per_query(u_hits, expect)
        weighted_pq.append((wr, wrel))
        uniform_pq.append((ur, urel))
        rr_deltas.append(mrr(wr, wrel) - mrr(ur, urel))
        ndcg_deltas.append(ndcg_at_k(wr, wrel, k) - ndcg_at_k(ur, urel, k))
        details.append({
            "query": r["query"], "type": r.get("query_type", ""), "expect": expect,
            "weighted_rank": w_rank, "uniform_rank": u_rank, "n_hits": len(w_hits),
        })

    def _agg(pq):
        return {"mrr": round(aggregate_mrr(pq), 3), f"ndcg@{k}": round(aggregate_ndcg(pq, k), 3)}

    w_m, u_m = _agg(weighted_pq), _agg(uniform_pq)
    # paired bootstrap CI: per-query (weighted-uniform) 差的均值 95% 区间。CI 全 > 0 → 加权显著优于
    # 等权(可决策); 含 0 → 当前样本不足以判方向(同 agent_e2e Gate A 的 CI 重叠语义)。点估 delta
    # 易被单 query 翻转带偏(n 小), CI 才是诚实的决策量([[recall-weight-ab-finding]] 的教训)。
    return {
        "suite": "recall", "status": "ok", "n": len(rows), "project_id": project_id,
        "metrics": {
            "weighted": w_m, "uniform": u_m,
            "mrr_delta": round(w_m["mrr"] - u_m["mrr"], 3),
            f"ndcg@{k}_delta": round(w_m[f"ndcg@{k}"] - u_m[f"ndcg@{k}"], 3),
            "mrr_delta_ci95": _bootstrap_ci(rr_deltas),
            f"ndcg@{k}_delta_ci95": _bootstrap_ci(ndcg_deltas),
        },
        "details": details,
    }
