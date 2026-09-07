"""codegraph 使用率计数回归 —— codegraph/server.py 自写代理早已往 codegraph_usage.jsonl
打点 (project_id/tool/ok/elapsed_ms), 但 health / --all 聚合侧曾硬编码 "not logged" 忽略它。
本测试锁住: ① 仓内 health 的 _usage_codegraph 读 jsonl 出计数; ② 平台 --all 的 _usage_7d
把 codegraph 按 project_id 汇总。
"""

from __future__ import annotations

import json

from codev_platform.ops.health._usage import _usage_codegraph
from codev_platform.ops.health._util import Report


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def test_usage_codegraph_counts_calls(tmp_path):
    cg = tmp_path / "codegraph_usage.jsonl"
    _write_jsonl(
        cg,
        [
            {
                "ts": "2999-01-01T10:00:00",
                "project_id": "p",
                "tool": "codegraph_search",
                "ok": True,
                "elapsed_ms": 12.0,
            },
            {
                "ts": "2999-01-01T10:01:00",
                "project_id": "p",
                "tool": "codegraph_search",
                "ok": True,
                "elapsed_ms": 8.0,
            },
            {
                "ts": "2999-01-01T10:02:00",
                "project_id": "p",
                "tool": "codegraph_status",
                "ok": False,
                "elapsed_ms": 4.0,
            },
        ],
    )
    r = Report()
    _usage_codegraph(r, cg)
    msg = next(row["msg"] for row in r.rows if row.get("tag") == "codegraph usage")
    assert "3 calls" in msg
    assert "codegraph_search=2" in msg
    assert "codegraph_status=1" in msg


def test_usage_codegraph_filters_by_project(tmp_path):
    cg = tmp_path / "codegraph_usage.jsonl"
    _write_jsonl(
        cg,
        [
            {
                "ts": "2999-01-01T10:00:00",
                "project_id": "a",
                "tool": "codegraph_search",
                "ok": True,
                "elapsed_ms": 5.0,
            },
            {
                "ts": "2999-01-01T10:01:00",
                "project_id": "b",
                "tool": "codegraph_search",
                "ok": True,
                "elapsed_ms": 5.0,
            },
            {
                "ts": "2999-01-01T10:02:00",
                "project_id": "a",
                "tool": "codegraph_impact",
                "ok": True,
                "elapsed_ms": 5.0,
            },
        ],
    )
    r = Report()
    _usage_codegraph(r, cg, project_id="a")
    msg = next(row["msg"] for row in r.rows if row.get("tag") == "codegraph usage")
    assert "2 calls" in msg  # 只算 project a 的两条, 不含 b
    assert "codegraph_search=1" in msg and "codegraph_impact=1" in msg


def test_usage_codegraph_missing_file_is_info(tmp_path):
    r = Report()
    _usage_codegraph(r, tmp_path / "nope.jsonl")
    row = next(row for row in r.rows if row.get("tag") == "codegraph usage")
    assert row["status"] == "INFO"
    assert r.red == 0


def test_platform_usage_7d_aggregates_codegraph(tmp_path, monkeypatch):
    """_usage_7d 把 codegraph_usage.jsonl 按 project_id 计入 'codegraph' key。"""
    from codev_platform import platform_status

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    cg = tmp_path / "logs" / "codegraph_usage.jsonl"
    _write_jsonl(
        cg,
        [
            {
                "ts": "2999-01-01T10:00:00",
                "project_id": "openclaw-stock",
                "tool": "codegraph_search",
                "ok": True,
            },
            {
                "ts": "2999-01-01T10:01:00",
                "project_id": "openclaw-stock",
                "tool": "codegraph_impact",
                "ok": True,
            },
        ],
    )
    usage = platform_status._usage_7d()
    assert usage["openclaw-stock"]["codegraph"] == 2
