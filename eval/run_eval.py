"""eval harness runner —— 量化检索 / 代码图谱 / 跨层链路的质量。

用法:
    python eval/run_eval.py --suite all
    python eval/run_eval.py --suite codegraph
    python eval/run_eval.py --suite retrieval            # 需 chroma daemon + 模型
    python eval/run_eval.py --suite all --json           # 机器可读输出

三套 suite:
- retrieval: 走已在跑的 chroma daemon (SSE), 算 recall@5 / hit@5 / MRR。
             daemon 没起 / 模型缺 -> 优雅报需要什么, 不崩 (status="skipped")。
- codegraph: 直接查 codegraph.db 的 nodes (无需 GPU), 算命中率。
- memory:    冲突消解 (纯逻辑) + 召回 (需 PG)。

数据集在 eval/datasets/*.jsonl。指标定义见 eval/metrics.py。
project_id: 默认 openclaw-stock (codegraph 真实链路数据在该项目);
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

from eval.metrics import accuracy, aggregate_mrr, hit_at_k, recall_at_k  # noqa: E402

_DATASETS = Path(__file__).resolve().parent / "datasets"
_DEFAULT_RETRIEVAL_PID = "codev-platform"
_DEFAULT_GRAPH_PID = "openclaw-stock"
# memory recall 子集用的隔离命名空间前缀 —— 只碰 eval 自己写的数据,清理按此 org_id + owner 删净。
_EVAL_MEM_ORG_PREFIX = "eval-mem-"


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
# memory suite —— 冲突消解 (纯逻辑, 始终可跑) + 召回 (需 PG, 缺则 skip)
# ---------------------------------------------------------------------------

def _entry_from_dict(d: dict):
    """dataset 里的 entry dict → MemoryEntry (id 占位空串, store.write 会补 uuid)。"""
    from codev_platform.agent.memory_store import MemoryEntry

    return MemoryEntry(
        id=d.get("id", ""),
        scope=d["scope"],
        scope_ref=d["scope_ref"],
        owner_user_id=d.get("owner_user_id", "eval"),
        content=d["content"],
        org_id=d.get("org_id", "default"),
        kind=d.get("kind"),
        topic_key=d.get("topic_key"),
        is_redline=d.get("is_redline", False),
        status=d.get("status", "active"),
        supersedes=d.get("supersedes"),
    )


def _run_memory_conflict(rows: list[dict]) -> dict:
    """冲突消解子集 (纯逻辑): resolve_conflicts -> 胜出条 content 比 expect_winner。"""
    from codev_platform.agent.memory_recall import resolve_conflicts

    correct = 0
    details = []
    for r in rows:
        # 只把 active 条目喂给 resolve_conflicts (superseded 不参与, 模拟真库 list_scope)
        entries = [_entry_from_dict(e) for e in r["entries"] if e.get("status", "active") == "active"]
        resolved = resolve_conflicts(entries, policy=r.get("policy", "personal_first"))
        # 取同 topic 组的胜出 (dataset 每条单 topic)
        winner = resolved[0].content if resolved else None
        ok = winner == r["expect_winner"]
        correct += 1 if ok else 0
        details.append({"note": r.get("note", ""), "expect": r["expect_winner"],
                        "got": winner, "ok": ok})
    total = len(rows)
    return {"correct": correct, "total": total,
            "resolution_accuracy": round(accuracy(correct, total), 3),
            "details": details}


def _run_memory_recall(rows: list[dict], k: int = 8) -> dict:
    """召回子集 (需 PG): 隔离命名空间写 seed -> recall -> hit/recall@k -> try/finally 清理。"""
    from codev_platform.core.config import env_or_config, load_config

    cfg = load_config()
    dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
    if not dsn:
        return {"status": "skipped",
                "reason": "memory.pg_dsn 未配 (~/.codev-platform/config.json)。conflict 子集已跑, recall 子集跳过。",
                "n": len(rows)}
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return {"status": "skipped",
                "reason": "当前解释器无 psycopg (pip install -e .[agent])。recall 子集跳过。",
                "n": len(rows)}

    read_dsn = env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
    from codev_platform.agent.memory_store import MemoryEntry
    from codev_platform.agent.memory_store_pg import SqlMemoryStore
    from codev_platform.agent.recall_service import LocalRecallService

    # 确定性隔离命名空间: 固定前缀 + 进程 pid (可复现 + 同机并发不撞)。别碰真数据。
    import os
    org_id = f"{_EVAL_MEM_ORG_PREFIX}{os.getpid()}"
    user_id = f"u-{os.getpid()}"
    project_id = f"p-{os.getpid()}"

    store = SqlMemoryStore(dsn, read_dsn=read_dsn)
    per_query: list[tuple[list[str], set]] = []
    details = []
    try:
        for r in rows:
            for s in r["seed"]:
                ref = s["scope_ref"].replace("__USER__", user_id).replace("__PROJ__", project_id)
                seed_id = store.write(MemoryEntry(
                    id="", scope=s["scope"], scope_ref=ref, owner_user_id=user_id,
                    content=s["content"], org_id=org_id, topic_key=s.get("topic_key"),
                    is_redline=s.get("is_redline", False),
                ))
                # supersede 场景: 写旧值后用 store.supersede 留痕换新值
                if r.get("supersede") and s.get("topic_key") == r["supersede"]["topic_key"]:
                    store.supersede(seed_id, MemoryEntry(
                        id="", scope=s["scope"], scope_ref=ref, owner_user_id=user_id,
                        content=r["supersede"]["content"], org_id=org_id,
                        topic_key=r["supersede"]["topic_key"],
                    ))
            recalled = LocalRecallService(store).recall(
                org_id=org_id, user_id=user_id, project_id=project_id,
                query=r.get("query", ""), limit=k)
            contents = [m.content for m in recalled]
            # 命中判定: 每个 expect_contains 关键词是否出现在某条召回 content 子串里
            expect = r["expect_contains"]
            retrieved_ids = [str(i) for i in range(len(contents))]
            relevant_ids = {str(i) for i, c in enumerate(contents)
                            if any(kw in c for kw in expect)}
            # recall@k 口径: 命中的期望关键词数 / 期望总数
            n_hit_kw = sum(1 for kw in expect if any(kw in c for c in contents))
            per_query.append((retrieved_ids, relevant_ids))
            details.append({"note": r.get("note", ""), "query": r.get("query", ""),
                            "expect": expect, "recalled": contents,
                            "hit": bool(relevant_ids),
                            "recall@k": round(n_hit_kw / len(expect), 3) if expect else 0.0})
    finally:
        # try/finally 清理: 按隔离 org_id + owner 删净 seed (含 supersede 留痕的旧行)
        cleaned = 0
        try:
            with store._write_pool.connection() as conn:
                cur = conn.execute(
                    "DELETE FROM memory_entries WHERE org_id=%s AND owner_user_id=%s",
                    (org_id, user_id))
                cleaned = cur.rowcount
        except Exception as ex:  # noqa: BLE001
            cleaned = -1
            details.append({"cleanup_error": str(ex)})

    n = len(per_query)
    return {
        "status": "ok",
        "n": n,
        "namespace": org_id,
        "cleaned_rows": cleaned,
        "metrics": {
            "recall@{}".format(k): round(sum(d["recall@k"] for d in details if "recall@k" in d) / n, 3) if n else 0.0,
            "hit@{}".format(k): round(sum(1 for d in details if d.get("hit")) / n, 3) if n else 0.0,
        },
        "details": details,
    }


def run_memory(k: int = 8) -> dict:
    rows = _load_jsonl("memory.jsonl")
    conflict_rows = [r for r in rows if r.get("kind") == "conflict"]
    recall_rows = [r for r in rows if r.get("kind") == "recall"]

    conflict = _run_memory_conflict(conflict_rows)
    recall = _run_memory_recall(recall_rows, k=k)

    metrics = {"resolution_accuracy": conflict["resolution_accuracy"]}
    if recall.get("status") == "ok":
        metrics.update(recall["metrics"])

    return {
        "suite": "memory",
        "status": "ok",  # conflict 子集始终可跑 -> suite 整体 ok
        "n": conflict["total"] + (recall.get("n", 0) if recall.get("status") == "ok" else 0),
        "metrics": metrics,
        "conflict": conflict,
        "recall": recall,
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
            print(f"    {mk:<20} = {mv}")
        # memory suite: 显式报告 recall 子集是否被 skip (PG 缺)
        if res["suite"] == "memory":
            rc = res.get("recall", {})
            if rc.get("status") == "skipped":
                print(f"    recall 子集 [SKIPPED] {rc.get('reason')}")
            elif rc.get("status") == "ok":
                print(f"    recall 子集 ok | seed cleaned_rows = {rc.get('cleaned_rows')} "
                      f"(namespace {rc.get('namespace')})")
        # 取一个代表性指标进总分 (recall@5 / recall / hit_rate)
        m = res["metrics"]
        primary = (m.get("recall@5") or m.get("recall") or m.get("hit_rate")
                   or m.get("resolution_accuracy") or 0.0)
        total_metric += primary
        total_ok += 1
    print("\n=== 总分 ===")
    if total_ok:
        print(f"  跑通 {total_ok} suite, 主指标均分 = {round(total_metric / total_ok, 3)}")
    else:
        print("  无 suite 跑通 (全部 skipped) —— 见上方 reason 准备后端。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="codev-platform eval harness")
    ap.add_argument("--suite", choices=["retrieval", "codegraph", "memory", "all"], default="all")
    ap.add_argument("--project", default=None, help="project_id 覆盖 (默认按 suite 选)")
    ap.add_argument("--json", action="store_true", help="机器可读 JSON 输出")
    ap.add_argument("-k", type=int, default=5, help="retrieval top-k (默认 5)")
    args = ap.parse_args(argv)

    suites = (["retrieval", "codegraph", "memory"]
              if args.suite == "all" else [args.suite])
    results: list[dict] = []
    for s in suites:
        if s == "retrieval":
            results.append(run_retrieval(args.project or _DEFAULT_RETRIEVAL_PID, k=args.k))
        elif s == "codegraph":
            results.append(run_codegraph(args.project or _DEFAULT_GRAPH_PID))
        elif s == "memory":
            results.append(run_memory())

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
