"""agent_trace jsonl → token 用量聚合(成本 / 缓存率)。Phase 8 计量看板数据源。

读 data_root/agent_trace/*.jsonl 的 `done` 事件 usage,按 last7d / allTime 窗口聚合:
总量 + 按模型 + 最近 N 条明细(供首页看板卡 + system 审计列表共用一个端点)。
成本按 `_PRICES`(per 1M token)估算,未知模型记 0(只报 token,不硬编未知单价)。
键用 camelCase 直接喂 web schema(同 platform_status.mcp_usage_report 的做法)。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

# per 1M token USD:(input_miss, input_hit, output)。未知模型 → None(只报 token 不报 $)。
# 单价会变,这里是估算口径;真账单以 provider 为准。改价只动这张表。
_PRICES: dict[str, tuple[float, float, float]] = {
    "deepseek-v4-flash": (0.14, 0.0028, 0.28),
    "deepseek-v4-pro": (0.435, 0.003625, 0.87),
    "deepseek-chat": (0.14, 0.0028, 0.28),  # 旧别名按 flash 估
}


def _cost(model: str, hit: int, miss: int, out: int) -> float:
    p = _PRICES.get(model)
    if not p:
        return 0.0
    miss_price, hit_price, out_price = p
    return miss / 1e6 * miss_price + hit / 1e6 * hit_price + out / 1e6 * out_price


def _iter_done(trace_dir: str | Path) -> Iterator[dict[str, Any]]:
    """遍历 agent_trace jsonl 的 done 事件(带 usage)。容错:坏行/坏文件跳过。"""
    d = Path(trace_dir)
    if not d.is_dir():
        return
    for f in sorted(d.glob("*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or '"done"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") == "done" and rec.get("usage"):
                yield rec


def _blank() -> dict[str, Any]:
    return {"queries": 0, "inputTokens": 0, "outputTokens": 0,
            "cacheHitTokens": 0, "cacheMissTokens": 0, "costUsd": 0.0}


def _finalize(b: dict[str, Any]) -> dict[str, Any]:
    ci = b["cacheHitTokens"] + b["cacheMissTokens"]
    b["cacheHitRate"] = round(b["cacheHitTokens"] / ci, 4) if ci else 0.0
    b["costUsd"] = round(b["costUsd"], 6)
    return b


def _accumulate(records: list[dict[str, Any]], since_ts: float, recent_limit: int = 50) -> dict[str, Any]:
    total = _blank()
    by_model: dict[str, dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for rec in records:
        ts = float(rec.get("ts", 0) or 0)
        if since_ts and ts < since_ts:
            continue
        u = rec["usage"]
        model = rec.get("model", "?")
        inp, out = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
        hit, miss = int(u.get("cache_hit_tokens", 0) or 0), int(u.get("cache_miss_tokens", 0) or 0)
        cost = _cost(model, hit, miss, out)
        bucket_model = by_model.setdefault(model, {**_blank(), "model": model})
        for b in (total, bucket_model):
            b["queries"] += 1
            b["inputTokens"] += inp
            b["outputTokens"] += out
            b["cacheHitTokens"] += hit
            b["cacheMissTokens"] += miss
            b["costUsd"] += cost
        recent.append({
            "ts": ts, "sessionId": rec.get("session_id", ""), "model": model,
            "steps": int(rec.get("steps", 0) or 0), "stopReason": rec.get("stop_reason", ""),
            "inputTokens": inp, "outputTokens": out, "cacheHitTokens": hit,
            "cacheMissTokens": miss, "costUsd": round(cost, 6),
        })
    _finalize(total)
    total["byModel"] = [_finalize(m) for m in sorted(by_model.values(), key=lambda x: -x["queries"])]
    recent.sort(key=lambda x: -x["ts"])
    total["recent"] = recent[:recent_limit]
    return total


def agent_usage_report(trace_dir: str | Path, now_ts: float | None = None) -> dict[str, Any]:
    """{last7d, allTime} 各:总量 + byModel + recent。camelCase 直喂 web schema。"""
    now = now_ts if now_ts is not None else time.time()
    records = list(_iter_done(trace_dir))
    return {
        "last7d": _accumulate(records, now - 7 * 86400),
        "allTime": _accumulate(records, 0),
    }
