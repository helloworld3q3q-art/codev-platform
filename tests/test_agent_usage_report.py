"""agent_usage_report 聚合单测 —— 造 trace jsonl, 固定 now_ts 控 7d 窗口, 验总量/按模型/缓存率/成本。"""
from __future__ import annotations

import json

from codev_platform.agent.usage_report import agent_usage_report

NOW = 1_000_000_000.0


def _write(tmp_path, records):
    d = tmp_path / "agent_trace"
    d.mkdir()
    with (d / "2026-06-11.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return d


def _done(ts, model, inp, out, hit, miss, sid="s"):
    return {"event": "done", "stop_reason": "answered", "steps": 3, "ts": ts,
            "session_id": sid, "provider": "deepseek", "model": model,
            "usage": {"input_tokens": inp, "output_tokens": out,
                      "cache_hit_tokens": hit, "cache_miss_tokens": miss}}


def test_window_filter_and_totals(tmp_path):
    d = _write(tmp_path, [
        _done(NOW - 3600, "deepseek-v4-flash", 1000, 50, 800, 200),       # 7d 内
        _done(NOW - 10 * 86400, "deepseek-v4-flash", 500, 20, 400, 100),  # 7d 外
    ])
    rep = agent_usage_report(d, now_ts=NOW)
    assert rep["last7d"]["queries"] == 1 and rep["allTime"]["queries"] == 2
    w = rep["last7d"]
    assert w["inputTokens"] == 1000 and w["cacheHitTokens"] == 800 and w["cacheMissTokens"] == 200
    assert w["cacheHitRate"] == 0.8  # 800/(800+200)
    # flash 成本: 200/1e6*0.14 + 800/1e6*0.0028 + 50/1e6*0.28 = 0.00004424, round6 = 0.000044
    assert w["costUsd"] == 0.000044


def test_by_model_and_recent(tmp_path):
    d = _write(tmp_path, [
        _done(NOW - 100, "deepseek-v4-flash", 100, 10, 80, 20, sid="a"),
        _done(NOW - 200, "deepseek-v4-pro", 100, 10, 80, 20, sid="b"),
        _done(NOW - 50, "deepseek-v4-flash", 100, 10, 80, 20, sid="c"),
    ])
    w = agent_usage_report(d, now_ts=NOW)["allTime"]
    assert w["queries"] == 3
    by = {m["model"]: m for m in w["byModel"]}
    assert by["deepseek-v4-flash"]["queries"] == 2 and by["deepseek-v4-pro"]["queries"] == 1
    # recent 按 ts 倒序: 最新(ts=NOW-50, sid=c)在首
    assert w["recent"][0]["sessionId"] == "c"
    assert len(w["recent"]) == 3


def test_unknown_model_zero_cost(tmp_path):
    d = _write(tmp_path, [_done(NOW - 100, "some-local-model", 100, 10, 80, 20)])
    w = agent_usage_report(d, now_ts=NOW)["allTime"]
    assert w["queries"] == 1 and w["costUsd"] == 0.0  # 未知模型只报 token 不报 $
    assert w["inputTokens"] == 100


def test_empty_dir(tmp_path):
    rep = agent_usage_report(tmp_path / "nope", now_ts=NOW)
    assert rep["last7d"]["queries"] == 0 and rep["allTime"]["queries"] == 0
