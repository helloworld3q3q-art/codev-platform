"""memory suite —— 冲突消解 (纯逻辑, 始终可跑) + 召回 (需 PG, 缺则 skip)。"""
from __future__ import annotations

from eval.metrics import accuracy
from eval.suites._common import load_jsonl

# memory recall 子集用的隔离命名空间前缀 —— 只碰 eval 自己写的数据, 清理按此 org_id + owner 删净。
_EVAL_MEM_ORG_PREFIX = "eval-mem-"


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
            f"recall@{k}": round(sum(d["recall@k"] for d in details if "recall@k" in d) / n, 3) if n else 0.0,
            f"hit@{k}": round(sum(1 for d in details if d.get("hit")) / n, 3) if n else 0.0,
        },
        "details": details,
    }


def run_memory(k: int = 8) -> dict:
    rows = load_jsonl("memory.jsonl")
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
