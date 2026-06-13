"""recall 查询可观测(Phase 8 measure-first 切片)。

每次 `recall_code` 记一条结构化 trace(query_id / 每 lane 耗时+候选数 / 融合数 / 无证据 / 总耗时)
到 `data_root/recall_trace/<date>.jsonl`, **best-effort 不阻断召回**(观测失败绝不拖垮高可用主路径)。
读侧 `recall_latency_report` 聚合 P50/P95 / 无证据回答率 / 每 lane 命中率与耗时(health/dashboard
数据源)。与 agent usage_report 同范式(单行 append JSONL / 窗口聚合 camelCase)。聚合纯计算可单测。

plan §八"先 baseline 再优化": 本切片只**量**(看慢在哪个 lane / 无证据率多高), 不做 cache 优化 ——
那留到这里有数据指出真实慢点后再动(不预先 over-build cache)。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterator


def _trace_dir() -> Path:
    from codev_platform.core.paths import data_root
    return data_root() / "recall_trace"


class RecallTrace:
    """累积一次 recall 的 per-lane 计时(纯数据记录器, 计时由 caller 做), finish 时 best-effort 落
    JSONL。**不抛**(召回高可用第一)。

    用法: t = RecallTrace(query, pid); 每 lane 跑完 t.add_lane(name, elapsed_ms, n_cand);
    融合后 t.finish(fused_count)。落盘 ts 用 wall clock(聚合按窗口过滤), total_ms 自构造起算。
    """

    def __init__(self, query: str, project_id: str) -> None:
        self._query = query
        self._pid = project_id
        self._lanes: list[dict[str, Any]] = []
        self._t0 = time.monotonic()

    def add_lane(self, name: str, ms: float, candidates: int) -> None:
        self._lanes.append({"lane": name, "ms": round(ms, 1), "candidates": int(candidates)})

    def finish(self, fused_count: int) -> None:
        rec = {
            "event": "recall", "ts": time.time(), "query_id": uuid.uuid4().hex[:12],
            "query": (self._query or "")[:200], "project_id": self._pid,
            "lanes": self._lanes, "fused": int(fused_count),
            "no_evidence": int(fused_count) == 0,
            "total_ms": round((time.monotonic() - self._t0) * 1000, 1),
        }
        try:
            d = _trace_dir()
            d.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec, ensure_ascii=False)
            with (d / f"{time.strftime('%Y-%m-%d')}.jsonl").open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:  # noqa: BLE001 — 观测绝不拖垮召回主路径(高可用)
            pass


# ---- 读侧聚合(health / dashboard 数据源)----

def _iter_recall(trace_dir: str | Path) -> Iterator[dict[str, Any]]:
    """遍历 recall_trace jsonl 的 recall 事件。容错: 坏行/坏文件跳过。"""
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
            if not line or '"recall"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("event") == "recall":
                yield rec


def _percentile(sorted_vals: list[float], p: float) -> float:
    """最近秩百分位(nearest-rank, 无插值): 空 → 0。p ∈ [0,1]。"""
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(round(p * (len(sorted_vals) - 1)))))
    return round(sorted_vals[idx], 1)


def _accumulate(records: list[dict[str, Any]], since_ts: float) -> dict[str, Any]:
    totals: list[float] = []          # 总耗时分布(算 P50/P95)
    no_evidence = 0
    n = 0
    lane_stat: dict[str, dict[str, Any]] = {}   # lane → {runs, hits, ms:[...]}
    for rec in records:
        ts = float(rec.get("ts", 0) or 0)
        if since_ts and ts < since_ts:
            continue
        n += 1
        totals.append(float(rec.get("total_ms", 0) or 0))
        if rec.get("no_evidence"):
            no_evidence += 1
        for lane in rec.get("lanes", []) or []:
            st = lane_stat.setdefault(lane.get("lane", "?"), {"runs": 0, "hits": 0, "ms": []})
            st["runs"] += 1
            if int(lane.get("candidates", 0) or 0) > 0:
                st["hits"] += 1
            st["ms"].append(float(lane.get("ms", 0) or 0))
    totals.sort()
    by_lane = []
    for name, st in sorted(lane_stat.items()):
        ms = sorted(st["ms"])
        by_lane.append({
            "lane": name, "runs": st["runs"],
            "hitRate": round(st["hits"] / st["runs"], 4) if st["runs"] else 0.0,
            "p50Ms": _percentile(ms, 0.5), "p95Ms": _percentile(ms, 0.95),
        })
    return {
        "queries": n,
        "p50Ms": _percentile(totals, 0.5), "p95Ms": _percentile(totals, 0.95),
        "noEvidenceRate": round(no_evidence / n, 4) if n else 0.0,
        "byLane": by_lane,
    }


def recall_latency_report(trace_dir: str | Path, now_ts: float | None = None) -> dict[str, Any]:
    """{last7d, allTime} 各: queries / p50Ms / p95Ms / noEvidenceRate / byLane[{lane,runs,hitRate,p50Ms,p95Ms}]。

    camelCase 直喂 web schema(同 agent_usage_report 做法)。plan §八 latency baseline + 无证据率看板数据源。
    """
    now = now_ts if now_ts is not None else time.time()
    records = list(_iter_recall(trace_dir))
    return {
        "last7d": _accumulate(records, now - 7 * 86400),
        "allTime": _accumulate(records, 0),
    }
