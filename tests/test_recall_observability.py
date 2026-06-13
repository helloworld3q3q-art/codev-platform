"""recall 查询可观测(Phase 8)—— 聚合 P50/P95/无证据率/lane 命中率 + writer 往返。"""
from __future__ import annotations

import json

from codev_platform.recall.observability import (
    RecallTrace,
    _percentile,
    recall_latency_report,
)


def _write_traces(d, recs):
    d.mkdir(parents=True, exist_ok=True)
    with (d / "2026-06-13.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps({"event": "recall", **r}) + "\n")


def test_percentile_nearest_rank():
    assert _percentile([], 0.5) == 0.0
    assert _percentile([42.0], 0.95) == 42.0
    assert _percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    assert _percentile([10.0, 20.0, 30.0, 40.0], 0.95) == 40.0


def test_recall_latency_report_aggregates(tmp_path):
    d = tmp_path / "recall_trace"
    _write_traces(d, [
        {"ts": 1000.0, "total_ms": 100.0, "no_evidence": False,
         "lanes": [{"lane": "graph", "ms": 50.0, "candidates": 3},
                   {"lane": "codegraph", "ms": 30.0, "candidates": 0},
                   {"lane": "vector", "ms": 20.0, "candidates": 5}]},
        {"ts": 1000.0, "total_ms": 300.0, "no_evidence": True,
         "lanes": [{"lane": "graph", "ms": 100.0, "candidates": 0},
                   {"lane": "vector", "ms": 100.0, "candidates": 0}]},
        {"ts": 1000.0, "total_ms": 200.0, "no_evidence": False,
         "lanes": [{"lane": "graph", "ms": 60.0, "candidates": 2}]},
    ])
    rep = recall_latency_report(d, now_ts=1000.0)["allTime"]
    assert rep["queries"] == 3
    assert rep["p50Ms"] == 200.0 and rep["p95Ms"] == 300.0      # 分布 [100,200,300]
    assert rep["noEvidenceRate"] == round(1 / 3, 4)            # 3 条里 1 条无证据
    graph = next(x for x in rep["byLane"] if x["lane"] == "graph")
    assert graph["runs"] == 3 and graph["hitRate"] == round(2 / 3, 4)   # graph 3 跑 2 命中
    cg = next(x for x in rep["byLane"] if x["lane"] == "codegraph")
    assert cg["runs"] == 1 and cg["hitRate"] == 0.0            # codegraph 1 跑 0 命中


def test_recall_latency_report_window_filters_old(tmp_path):
    d = tmp_path / "recall_trace"
    _write_traces(d, [
        {"ts": 100.0, "total_ms": 999.0, "no_evidence": False, "lanes": []},        # 7 天前
        {"ts": 1_000_000.0, "total_ms": 50.0, "no_evidence": False, "lanes": []},   # 近期
    ])
    rep = recall_latency_report(d, now_ts=1_000_000.0)
    assert rep["last7d"]["queries"] == 1 and rep["last7d"]["p50Ms"] == 50.0   # 仅近期
    assert rep["allTime"]["queries"] == 2                                     # 全量含旧


def test_recall_latency_report_empty_dir(tmp_path):
    rep = recall_latency_report(tmp_path / "nope", now_ts=1000.0)
    assert rep["allTime"]["queries"] == 0 and rep["allTime"]["noEvidenceRate"] == 0.0


def test_recall_trace_writer_roundtrip(tmp_path, monkeypatch):
    # RecallTrace.finish 落一条可被 report 读回的 trace(monkeypatch trace 目录, 不碰真 data_root)。
    d = tmp_path / "recall_trace"
    monkeypatch.setattr("codev_platform.recall.observability._trace_dir", lambda: d)
    t = RecallTrace("find user save", "demo")
    t.add_lane("graph", 12.3, 4)
    t.finish(0)                                    # 融合空 → no_evidence
    rep = recall_latency_report(d)["allTime"]
    assert rep["queries"] == 1 and rep["noEvidenceRate"] == 1.0
    assert next(x for x in rep["byLane"] if x["lane"] == "graph")["hitRate"] == 1.0


def test_recall_trace_finish_never_raises(monkeypatch):
    # 观测高可用红线: 落盘失败(目录不可写等)绝不抛 → 不拖垮召回。
    def _boom():
        raise OSError("disk full")
    monkeypatch.setattr("codev_platform.recall.observability._trace_dir", _boom)
    RecallTrace("q", "p").finish(0)                # 不抛即通过
