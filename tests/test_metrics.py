"""ops.metrics 纯聚合层单测 —— 喂 fake records, 不读真文件。"""
from __future__ import annotations

import json

from codev_platform.ops import metrics as m


def test_parse_lines_skips_bad_and_nondict():
    lines = [
        json.dumps({"ts": "2026-06-01T10:00:00", "ok": True}),
        "   ",                       # 空行跳过
        "{not json",                 # 坏行跳过
        json.dumps([1, 2, 3]),       # 非 dict 跳过
        json.dumps({"tool": "x"}),
    ]
    recs = m.parse_lines(lines)
    assert len(recs) == 2
    assert recs[0]["ok"] is True
    assert recs[1]["tool"] == "x"


def test_parse_since():
    assert m.parse_since(None) is None
    assert m.parse_since("") is None
    assert m.parse_since("30m") == 1800
    assert m.parse_since("2h") == 7200
    assert m.parse_since("1d") == 86400
    assert m.parse_since("90s") == 90
    assert m.parse_since("45") == 45  # bare = 秒


def test_within_since_filters_by_ts():
    now = 1_000_000.0
    recs = [
        {"ts": "x"},  # ts 缺省字段保留处理见下
        {"ts": "1970-01-12T13:46:40+00:00"},  # epoch 1_000_000 (在窗内)
        {"ts": "1970-01-01T00:00:00+00:00"},  # epoch 0 (远早, 过滤掉)
    ]
    # since None -> 全量
    assert len(m.within_since(recs, now, None)) == 3
    # since 100 秒: 只保留 cutoff(=999900) 之后的 + 不可解析 ts 的(保留)
    kept = m.within_since(recs, now, 100)
    assert {"ts": "1970-01-12T13:46:40+00:00"} in kept
    assert {"ts": "x"} in kept            # 不可解析 -> 保留
    assert {"ts": "1970-01-01T00:00:00+00:00"} not in kept  # 远早 -> 过滤


def test_aggregate_audit_counts_and_deny_reasons():
    audit = [
        {"allowed": True, "reason": "token ok"},
        {"allowed": False, "reason": "org mismatch"},
        {"allowed": False, "reason": "org mismatch"},
        {"allowed": False, "reason": "no token"},
    ]
    s = m.aggregate({"audit": audit})
    a = s.per_source["audit"]
    assert a["total"] == 4
    assert a["allowed"] == 1
    assert a["denied"] == 3
    assert a["deny_by_reason"]["org mismatch"] == 2
    assert a["deny_by_reason"]["no token"] == 1
    assert s.totals["audit_total"] == 4


def test_aggregate_usage_total_by_tool_and_errors():
    cross = [
        {"tool": "find_table_refs", "ok": True},
        {"tool": "find_table_refs", "ok": False},
        {"tool": "search_nodes", "ok": True},
    ]
    chroma = [{"hit": 3}, {"hit": 0}]
    codegraph = [{"tool": "codegraph_search", "ok": True}]
    s = m.aggregate({"cross-link": cross, "chroma": chroma, "codegraph": codegraph})
    c = s.per_source["cross-link"]
    assert c["total"] == 3
    assert c["errors"] == 1
    assert c["by_tool"]["find_table_refs"] == 2
    assert s.per_source["chroma"]["total"] == 2  # 召回只计次数
    assert s.totals["mcp_calls"] == 3 + 2 + 1
    assert s.totals["mcp_errors"] == 1


def test_check_alerts_deny_rate():
    s = m.aggregate({"audit": [
        {"allowed": False, "reason": "x"},
        {"allowed": True, "reason": "y"},
    ]})  # deny_rate = 0.5
    assert m.check_alerts(s, {"deny_rate_max": 0.2})  # 0.5 > 0.2 -> 告警
    assert not m.check_alerts(s, {"deny_rate_max": 0.8})  # 0.5 < 0.8 -> 无
    assert not m.check_alerts(s, {})       # 空阈值 -> 宽松不告警
    assert not m.check_alerts(s, None)     # 缺省 -> 不告警


def test_check_alerts_error_rate():
    s = m.aggregate({"codegraph": [
        {"tool": "a", "ok": False},
        {"tool": "b", "ok": True},
    ]})  # error_rate = 0.5
    alerts = m.check_alerts(s, {"error_rate_max": 0.1})
    assert any("codegraph" in a for a in alerts)
    assert not m.check_alerts(s, {"error_rate_max": 0.9})
