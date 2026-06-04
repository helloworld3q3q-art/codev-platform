"""platform_status.mcp_usage_report —— MCP 调用分析聚合(仪表盘数据源)。

锁住: 每项目 + 合计; 7天窗 vs 全时段累计; chroma 按 agent/dev 分桶 + 命中;
cross-link/codegraph 纯调用数; 自部署模型 embed/rerank 计数。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from codev_platform import platform_status


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))  # logs_dir() = tmp/logs
    now = datetime.now().isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    _write(tmp_path / "logs" / "search_recall.jsonl", [
        {"ts": now, "project_id": "p1", "client": "agent", "hit": 1, "rerank_used": True},
        {"ts": now, "project_id": "p1", "client": "dev", "hit": 1, "rerank_used": False},
        {"ts": now, "project_id": "p1", "hit": 0},                      # 无 client → dev, 未命中
        {"ts": old, "project_id": "p1", "client": "dev", "hit": 1},     # 仅入 allTime
    ])
    repo = tmp_path / "repo"
    _write(repo / "codev_platform" / "cross_link" / "cross_link_usage.jsonl", [
        {"ts": now, "project_id": "p1"}, {"ts": now, "project_id": "p2"},
    ])
    _write(repo / "codev_platform" / "codegraph" / "codegraph_usage.jsonl", [
        {"ts": now, "project_id": "p1"},
    ])
    return platform_status.mcp_usage_report(repo)


def _proj(window, pid):
    return next(p for p in window["projects"] if p["projectId"] == pid)


def test_last7d_chroma_agent_dev_split_and_hits(tmp_path, monkeypatch):
    rep = _setup(tmp_path, monkeypatch)
    p1 = _proj(rep["last7d"], "p1")
    assert p1["chroma"]["agentCalls"] == 1
    assert p1["chroma"]["devCalls"] == 2          # 1 显式 dev + 1 无 client
    assert p1["chroma"]["hits"] == 2              # 两条 recent hit=1


def test_last7d_model_counts(tmp_path, monkeypatch):
    rep = _setup(tmp_path, monkeypatch)
    p1 = _proj(rep["last7d"], "p1")
    assert p1["model"]["embedCalls"] == 3         # 3 条 recent 搜索
    assert p1["model"]["rerankCalls"] == 1        # 仅 1 条 rerank_used


def test_cross_link_and_codegraph_calls(tmp_path, monkeypatch):
    rep = _setup(tmp_path, monkeypatch)
    p1 = _proj(rep["last7d"], "p1")
    assert p1["crossLink"]["calls"] == 1
    assert p1["codegraph"]["calls"] == 1


def test_alltime_includes_old_entry(tmp_path, monkeypatch):
    rep = _setup(tmp_path, monkeypatch)
    p1_7d = _proj(rep["last7d"], "p1")
    p1_all = _proj(rep["allTime"], "p1")
    assert p1_7d["chroma"]["devCalls"] == 2       # 老条目不入 7d
    assert p1_all["chroma"]["devCalls"] == 3      # 老条目入 allTime
    assert p1_all["model"]["embedCalls"] == 4


def test_total_sums_all_projects(tmp_path, monkeypatch):
    rep = _setup(tmp_path, monkeypatch)
    total = rep["last7d"]["total"]
    assert total["crossLink"]["calls"] == 2       # p1 1 + p2 1
    assert total["chroma"]["agentCalls"] == 1
    assert total["model"]["embedCalls"] == 3
