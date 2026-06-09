"""retrieval suite —— 走已在跑的 chroma daemon (SSE), 算 recall@5 / hit@5 / MRR。

daemon 没起 / 模型缺 / 超时 → 优雅 status="skipped"(报需要什么, 不崩)。
"""
from __future__ import annotations

import asyncio
import json

from eval.metrics import aggregate_mrr, hit_at_k, recall_at_k
from eval.suites._common import load_jsonl


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
    rows = load_jsonl("retrieval.jsonl")
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
            f"recall@{k}": round(sum(recall_at_k(rt, rl, k) for rt, rl in per_query) / n, 3),
            f"hit@{k}": round(sum(1 for rt, rl in per_query if hit_at_k(rt, rl, k)) / n, 3),
            "mrr": round(aggregate_mrr(per_query), 3),
        },
        "details": details,
    }
