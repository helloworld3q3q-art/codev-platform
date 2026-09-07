"""search_recall 调用按来源(agent / dev)分桶回归。

web 端 agent(chat 的 search_docs 工具)与开发端 Claude Code 直调混在一起会让采纳率失真。
本测试锁住: ① search_recall 行按 client 分桶(无 client 字段 → 计 dev, 向后兼容);
② platform-docs usage 行 dev/agent 拆分; ③ 采纳率 dev_search_docs_per_candidate
**只算 dev 侧, agent 排除**(agent 是产品流量, 不算开发者用 MCP 替代 grep)。
"""
from __future__ import annotations

import json
import subprocess

from codev_platform.ops.health._usage import _usage_platform_docs, _usage_search_recall
from codev_platform.ops.health._util import Report


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for o in rows:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def _recall_rows():
    base = {"ts": "2999-01-01T10:00:00", "project_id": "p", "hit": 1, "top5": [{"distance": 0.5}]}
    return [
        {**base, "client": "agent"},
        {**base, "client": "dev"},
        {**base, "client": "dev"},
        {**base},  # 无 client 字段 → 向后兼容计 dev
    ]


def _msg(r, tag):
    return next(row["msg"] for row in r.rows if row.get("tag") == tag)


def test_search_recall_buckets_agent_dev(tmp_path):
    f = tmp_path / "search_recall.jsonl"
    _write_jsonl(f, _recall_rows())
    r = Report()
    _usage_search_recall(r, f, project_id="p")
    msg = _msg(r, "search_recall")
    assert "4 queries" in msg
    assert "agent 1" in msg
    assert "dev 3" in msg  # 2 显式 dev + 1 无 client


def test_platform_usage_line_splits_dev_agent(tmp_path):
    f = tmp_path / "search_recall.jsonl"
    _write_jsonl(f, _recall_rows())
    r = Report()
    # tmp_path 非 git repo → commit_count=0(不影响 recall 分桶计数)
    _usage_platform_docs(r, tmp_path, f, health={}, project_id="p")
    msg = _msg(r, "platform-docs usage")
    assert "dev 3" in msg and "agent 1" in msg


def test_adopt_ratio_excludes_agent(tmp_path):
    # 造一个 .claude/rules/*.md 候选 commit(命中 default_cand), 采纳率应 = dev 3 / 候选 1 = 3.0,
    # agent 1 不计入(dev-side only)。
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "x.md").write_text("rule", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "docs(rules): x"], cwd=repo, check=True)

    f = repo / "search_recall.jsonl"
    _write_jsonl(f, _recall_rows())
    r = Report()
    _usage_platform_docs(r, repo, f, health={}, project_id="p")
    msg = _msg(r, "platform-docs adopt")
    assert "dev_search_docs_per_candidate=3.0" in msg
    assert "agent 1 excluded" in msg
