"""Trace.done 落 usage(token+缓存)单测 —— Phase 8 per-租户计量地基。"""
from __future__ import annotations

import json

from codev_platform.agent.trace import Trace


def _read_records(tmp_path):
    files = list((tmp_path / "agent_trace").glob("*.jsonl"))
    assert files, "trace jsonl 未生成"
    return [json.loads(ln) for ln in files[0].read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_done_writes_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    t = Trace("sess1", "deepseek", "deepseek-v4-flash")
    t.done("answered", 3, {"input_tokens": 100, "output_tokens": 10,
                           "cache_hit_tokens": 80, "cache_miss_tokens": 20})
    rec = _read_records(tmp_path)[-1]
    assert rec["event"] == "done" and rec["steps"] == 3
    assert rec["session_id"] == "sess1" and rec["model"] == "deepseek-v4-flash"
    u = rec["usage"]
    assert u["input_tokens"] == 100 and u["cache_hit_tokens"] == 80 and u["cache_miss_tokens"] == 20


def test_done_without_usage_has_no_usage_key(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    Trace("s", "p", "m").done("max_steps", 12)
    rec = _read_records(tmp_path)[-1]
    assert rec["event"] == "done" and "usage" not in rec


def test_done_writes_tenant_identity(monkeypatch, tmp_path):
    # org_id/user_id 随 trace 落盘 → per-租户计量地基(身份由 ChatService 从请求身份传入)。
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    Trace("sess", "deepseek", "deepseek-v4-flash", org_id="acme", user_id="u1").done(
        "answered", 3, {"input_tokens": 100, "output_tokens": 10,
                        "cache_hit_tokens": 80, "cache_miss_tokens": 20})
    rec = _read_records(tmp_path)[-1]
    assert rec["org_id"] == "acme" and rec["user_id"] == "u1"


def test_done_omits_identity_when_not_given(monkeypatch, tmp_path):
    # 不给身份(旧调用 / CLI 单机)→ 不写 org_id/user_id 键(下游归 unknown 桶, 不臆造)。
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    Trace("s", "p", "m").done("answered", 1, {"input_tokens": 1, "output_tokens": 1})
    rec = _read_records(tmp_path)[-1]
    assert "org_id" not in rec and "user_id" not in rec
