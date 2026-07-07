"""codegraph suite —— 直查 codegraph.db 的 nodes/FTS (无需 GPU), 算命中率 / MRR。"""
from __future__ import annotations

import sqlite3

from eval.metrics import aggregate_mrr
from eval.suites._common import load_jsonl


def _codegraph_search(conn: sqlite3.Connection, query: str, spec, limit: int = 10) -> list[str]:
    """模拟 codegraph_search: 按 name / qualified_name LIKE 召回。

    返回 'name | qualified_name | file_path' 列表 (含 qualified_name, 与真实
    codegraph_search 一样能透出符号所属类 / 模块, 命中判定更可信)。
    """
    cur = conn.execute(
        """SELECT name, qualified_name, file_path FROM nodes
           WHERE name LIKE ? OR qualified_name LIKE ?
           ORDER BY (name = ?) DESC, length(name) ASC
           LIMIT ?""",
        (f"%{query}%", f"%{query}%", query, limit),
    )
    out = []
    for name, qn, fp in cur.fetchall():
        out.append(f"{name} | {spec.local_ref(qn or name)} | {spec.local_file(fp)}")
    return out


def run_codegraph(project_id: str) -> dict:
    from codev_platform.core.repos import project_codegraph_dbs

    dbs = project_codegraph_dbs(project_id)
    rows = load_jsonl("codegraph.jsonl")
    if not dbs:
        return {
            "suite": "codegraph",
            "status": "skipped",
            "reason": f"project={project_id} 未找到可读 codegraph.db。"
                      f"先跑 `codev-platform reindex --codegraph` (或 codegraph sync) 建索引, "
                      f"并 `codev-platform codegraph link` 建联接。",
            "n": len(rows),
        }
    per_query: list[tuple[list[str], set]] = []
    details = []
    for r in rows:
        hits = []
        for spec, db_path in dbs:
            conn = sqlite3.connect(db_path)
            try:
                hits.extend(_codegraph_search(conn, r["query"], spec))
            finally:
                conn.close()
        expect = r["expect_symbol_or_file"]
        # 命中判定: 任一结果的 'name | file' 字符串含 expect 子串
        matched = [h for h in hits if expect in h]
        # 用合成 id: 命中则视为相关 id 在召回里
        retrieved_ids = list(range(len(hits)))
        relevant_ids = {i for i, h in enumerate(hits) if expect in h}
        per_query.append(([str(i) for i in retrieved_ids], {str(i) for i in relevant_ids}))
        details.append({
            "query": r["query"],
            "expect": expect,
            "hit": bool(matched),
            "first_match_rank": (next((i for i, h in enumerate(hits) if expect in h), -1)),
            "n_hits": len(hits),
        })
    n = len(per_query)
    return {
        "suite": "codegraph",
        "status": "ok",
        "n": n,
        "project_id": project_id,
        "metrics": {
            "hit_rate": round(sum(1 for d in details if d["hit"]) / n, 3),
            "mrr": round(aggregate_mrr(per_query), 3),
        },
        "details": details,
    }
