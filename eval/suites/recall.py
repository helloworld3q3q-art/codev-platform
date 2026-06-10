"""recall suite —— 跨 lane 融合召回 A/B 验 planner 权重 (Phase 6)。

加权(planner 自动)vs 等权, 比 MRR / nDCG@k + delta。落地 Phase 6 Gate「相比 baseline 可
量化提升」+ 验 `_PREFER` 是否真有效。两 lane(graph + codegraph)直读本地 sqlite(免 daemon);
任一 store 缺 → skip(单 lane 下加权无意义, 比较不成立)。
"""
from __future__ import annotations

from eval.metrics import aggregate_mrr, aggregate_ndcg
from eval.suites._common import load_jsonl, token_match

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
    from codev_platform.core.paths import codegraph_db_path
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
    if not graph_store_path(project_id).exists() or not codegraph_db_path(project_id).exists():
        return {
            "suite": "recall", "status": "skipped", "n": len(rows),
            "reason": f"需 graph store + codegraph.db 双 lane (project={project_id}); "
                      f"缺一则单 lane, 加权 vs 等权比较不成立。",
        }
    uniform = {GRAPH_LANE: 1.0, CODEGRAPH_LANE: 1.0}
    weighted_pq: list[tuple[list[str], set[str]]] = []
    uniform_pq: list[tuple[list[str], set[str]]] = []
    details = []
    for r in rows:
        expect = r["expect"]
        w_hits = recall_code(r["query"], project_id, weights=None, limit=max(k, 10))   # planner 自动
        u_hits = recall_code(r["query"], project_id, weights=uniform, limit=max(k, 10))
        wr, wrel, w_rank = _recall_per_query(w_hits, expect)
        ur, urel, u_rank = _recall_per_query(u_hits, expect)
        weighted_pq.append((wr, wrel))
        uniform_pq.append((ur, urel))
        details.append({
            "query": r["query"], "type": r.get("query_type", ""), "expect": expect,
            "weighted_rank": w_rank, "uniform_rank": u_rank, "n_hits": len(w_hits),
        })

    def _agg(pq):
        return {"mrr": round(aggregate_mrr(pq), 3), f"ndcg@{k}": round(aggregate_ndcg(pq, k), 3)}

    w_m, u_m = _agg(weighted_pq), _agg(uniform_pq)
    return {
        "suite": "recall", "status": "ok", "n": len(rows), "project_id": project_id,
        "metrics": {
            "weighted": w_m, "uniform": u_m,
            "mrr_delta": round(w_m["mrr"] - u_m["mrr"], 3),
            f"ndcg@{k}_delta": round(w_m[f"ndcg@{k}"] - u_m[f"ndcg@{k}"], 3),
        },
        "details": details,
    }
