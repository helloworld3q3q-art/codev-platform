"""eval harness runner —— 量化检索 / 代码图谱 / 跨层链路的质量。

用法:
    python eval/run_eval.py --suite all
    python eval/run_eval.py --suite crosslink            # 无 GPU 即可跑
    python eval/run_eval.py --suite codegraph
    python eval/run_eval.py --suite retrieval            # 需 chroma daemon + 模型
    python eval/run_eval.py --suite all --json           # 机器可读输出

三套 suite:
- retrieval: 走已在跑的 chroma daemon (SSE), 算 recall@5 / hit@5 / MRR。
             daemon 没起 / 模型缺 -> 优雅报需要什么, 不崩 (status="skipped")。
- crosslink: 直接查 cross_layer.sqlite (无需 GPU), 算命中率。
- codegraph: 直接查 codegraph.db 的 nodes (无需 GPU), 算命中率。

数据集在 eval/datasets/*.jsonl。指标定义见 eval/metrics.py。
project_id: 默认 openclaw-stock (cross-link / codegraph 真实链路数据在该项目);
            retrieval 默认 codev-platform (平台自身文档已索引)。可用 --project 覆盖。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

# 允许 `python eval/run_eval.py` 直接跑 (把仓根加进 path)
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.metrics import aggregate_mrr, hit_at_k, recall_at_k  # noqa: E402

_DATASETS = Path(__file__).resolve().parent / "datasets"
_DEFAULT_RETRIEVAL_PID = "codev-platform"
_DEFAULT_GRAPH_PID = "openclaw-stock"


def _load_jsonl(name: str) -> list[dict]:
    path = _DATASETS / name
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# retrieval suite (chroma daemon, 需模型)
# ---------------------------------------------------------------------------

def _daemon_sse_url() -> str:
    from codev_platform.core.config import get, load_config
    port = get(load_config(), "daemon.port", 18083)
    return f"http://127.0.0.1:{port}/sse"


async def _search_once(query: str, project_id: str, k: int) -> list[str]:
    """调 daemon search_docs, 返回命中的 file 路径列表 (按 rank)。"""
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    url = f"{_daemon_sse_url()}?project_id={project_id}"
    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_docs", {"query": query, "k": k})
    texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    payload = json.loads(texts[0]) if texts else []
    if not isinstance(payload, list):
        return []
    return [(h or {}).get("file") for h in payload if (h or {}).get("file")]


def run_retrieval(project_id: str, k: int = 5) -> dict:
    rows = _load_jsonl("retrieval.jsonl")
    try:
        per_query: list[tuple[list[str], set]] = []
        details = []
        for r in rows:
            retrieved = asyncio.run(
                asyncio.wait_for(_search_once(r["query"], project_id, k), timeout=90)
            )
            relevant = set(r["relevant"])
            per_query.append((retrieved, relevant))
            details.append({
                "query": r["query"],
                "hit": hit_at_k(retrieved, relevant, k),
                "recall@k": round(recall_at_k(retrieved, relevant, k), 3),
                "top": retrieved[:k],
            })
    except Exception as e:  # noqa: BLE001 — daemon 没起 / 模型缺 / 超时, 优雅降级
        return {
            "suite": "retrieval",
            "status": "skipped",
            "reason": f"chroma daemon 不可用 ({type(e).__name__}: {e})。"
                      f"先跑 `codev-platform serve-mcp start` 拉起 daemon (预热模型 ~30-60s), "
                      f"或确认模型路径 (config models.embed_path)。",
            "n": len(rows),
        }
    n = len(per_query)
    return {
        "suite": "retrieval",
        "status": "ok",
        "n": n,
        "k": k,
        "project_id": project_id,
        "metrics": {
            "recall@{}".format(k): round(sum(recall_at_k(rt, rl, k) for rt, rl in per_query) / n, 3),
            "hit@{}".format(k): round(sum(1 for rt, rl in per_query if hit_at_k(rt, rl, k)) / n, 3),
            "mrr": round(aggregate_mrr(per_query), 3),
        },
        "details": details,
    }


# ---------------------------------------------------------------------------
# crosslink suite (sqlite 直查, 无 GPU)
# ---------------------------------------------------------------------------

def run_crosslink(project_id: str) -> dict:
    from codev_platform.core.paths import cross_link_db_path
    from codev_platform.cross_link.query import CrossLayerDB

    db_path = cross_link_db_path(project_id)
    rows = _load_jsonl("crosslink.jsonl")
    if not db_path.exists():
        return {
            "suite": "crosslink",
            "status": "skipped",
            "reason": f"cross_layer.sqlite 不存在: {db_path}。"
                      f"先跑 `codev-platform reindex --cross-link` (或 cross_link.build_index) 建索引。",
            "n": len(rows),
        }
    conn = sqlite3.connect(db_path)
    db = CrossLayerDB(conn)
    per_query: list[tuple[list[str], set]] = []
    details = []
    try:
        for r in rows:
            key, expect = r["key"], set(r["expect_contains"])
            if r["kind"] == "table":
                refs = db.find_all_references(key)
                names = [
                    x["name"]
                    for group in refs.values()
                    for x in group
                ]
            elif r["kind"] == "endpoint":
                names = [x["name"] for x in db.list_endpoint_callers(key)]
            else:
                names = []
            per_query.append((names, expect))
            details.append({
                "kind": r["kind"],
                "key": key,
                "recall": round(recall_at_k(names, expect, len(names) or 1), 3),
                "hit": hit_at_k(names, expect, len(names) or 1),
            })
    finally:
        db.close()
    n = len(per_query)
    # crosslink 用全量召回 (k = 命中列表长度), 衡量 "期望的链路是否都被索引到"
    return {
        "suite": "crosslink",
        "status": "ok",
        "n": n,
        "project_id": project_id,
        "metrics": {
            "recall": round(sum(recall_at_k(rt, rl, len(rt) or 1) for rt, rl in per_query) / n, 3),
            "hit_rate": round(sum(1 for rt, rl in per_query if hit_at_k(rt, rl, len(rt) or 1)) / n, 3),
        },
        "details": details,
    }


# ---------------------------------------------------------------------------
# codegraph suite (codegraph.db 直查 nodes/FTS, 无 GPU)
# ---------------------------------------------------------------------------

def _codegraph_search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[str]:
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
        out.append(f"{name} | {qn} | {fp}")
    return out


def run_codegraph(project_id: str) -> dict:
    from codev_platform.core.paths import codegraph_db_path

    db_path = codegraph_db_path(project_id)
    rows = _load_jsonl("codegraph.jsonl")
    if not db_path.exists():
        return {
            "suite": "codegraph",
            "status": "skipped",
            "reason": f"codegraph.db 不存在: {db_path}。"
                      f"先跑 `codev-platform reindex --codegraph` (或 codegraph sync) 建索引, "
                      f"并 `codev-platform codegraph link` 建联接。",
            "n": len(rows),
        }
    conn = sqlite3.connect(db_path)
    per_query: list[tuple[list[str], set]] = []
    details = []
    try:
        for r in rows:
            hits = _codegraph_search(conn, r["query"])
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
    finally:
        conn.close()
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


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def _print_human(results: list[dict]) -> None:
    total_ok = 0
    total_metric = 0.0
    for res in results:
        suite = res["suite"]
        print(f"\n=== suite: {suite} ===")
        if res["status"] == "skipped":
            print(f"  [SKIPPED] {res['reason']}")
            print(f"  ({res['n']} cases 未跑)")
            continue
        print(f"  status: ok | cases: {res['n']} | project: {res.get('project_id')}")
        for mk, mv in res["metrics"].items():
            print(f"    {mk:<12} = {mv}")
        # 取一个代表性指标进总分 (recall@5 / recall / hit_rate)
        m = res["metrics"]
        primary = m.get("recall@5") or m.get("recall") or m.get("hit_rate") or 0.0
        total_metric += primary
        total_ok += 1
    print("\n=== 总分 ===")
    if total_ok:
        print(f"  跑通 {total_ok} suite, 主指标均分 = {round(total_metric / total_ok, 3)}")
    else:
        print("  无 suite 跑通 (全部 skipped) —— 见上方 reason 准备后端。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="codev-platform eval harness")
    ap.add_argument("--suite", choices=["retrieval", "crosslink", "codegraph", "all"], default="all")
    ap.add_argument("--project", default=None, help="project_id 覆盖 (默认按 suite 选)")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("-k", type=int, default=5, help="retrieval top-k (默认 5)")
    args = ap.parse_args(argv)

    suites = ["retrieval", "crosslink", "codegraph"] if args.suite == "all" else [args.suite]
    results: list[dict] = []
    for s in suites:
        if s == "retrieval":
            results.append(run_retrieval(args.project or _DEFAULT_RETRIEVAL_PID, k=args.k))
        elif s == "crosslink":
            results.append(run_crosslink(args.project or _DEFAULT_GRAPH_PID))
        elif s == "codegraph":
            results.append(run_codegraph(args.project or _DEFAULT_GRAPH_PID))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
