"""recall suite(eval/run_eval.py:run_recall)纯逻辑单测 + dataset 结构校验。

A/B(加权 vs 等权)的真实数值要在 WSL 真实双 lane store 上跑; 这里只钉 harness 逻辑 +
golden 形状, 不依赖 store。
"""
from __future__ import annotations

import json
from pathlib import Path

from codev_platform.recall.service import CodeRecallHit
from eval.suites.recall import _recall_per_query

_DATASET = Path(__file__).resolve().parents[1] / "eval" / "datasets" / "recall.jsonl"


def _hit(ref, name="", file=None):
    return CodeRecallHit(ref=ref, score=1.0, name=name, kind="x", file=file)


def test_recall_per_query_matches_by_name():
    hits = [_hit("h0", name="other"), _hit("h1", name="weighted_rrf"), _hit("h2", name="foo")]
    retrieved, relevant, rank = _recall_per_query(hits, "weighted_rrf")
    assert retrieved == ["0", "1", "2"]
    assert relevant == {"1"} and rank == 2          # 首个相关在 rank2(1-based)


def test_recall_per_query_matches_by_file():
    hits = [_hit("h0", name="x", file="a.py"),
            _hit("h1", name="y", file="codev_platform/graph/audit.py")]
    _, relevant, rank = _recall_per_query(hits, "audit")
    assert relevant == {"1"} and rank == 2           # file 含 audit 也算相关


def test_recall_per_query_excludes_test_hits():
    # #1: 同名测试(test_ 函数 / tests 目录)即使含 expect 子串也不算相关 —— 只标真实现 ref。
    hits = [
        _hit("h0", name="test_weighted_rrf", file="tests/test_recall_fusion.py"),  # 测试函数
        _hit("h1", name="weighted_rrf", file="codev_platform/recall/fusion.py"),    # 真实现
        _hit("h2", name="helper", file="tests/test_x.py"),                          # tests 目录
    ]
    _, relevant, rank = _recall_per_query(hits, "weighted_rrf")
    assert relevant == {"1"} and rank == 2     # 只命中真实现, 两个测试 hit 被排除


def test_recall_per_query_no_match():
    hits = [_hit("h0", name="x", file="a.py")]
    retrieved, relevant, rank = _recall_per_query(hits, "nonexistent")
    assert relevant == set() and rank == -1


def test_recall_per_query_empty_hits():
    assert _recall_per_query([], "anything") == ([], set(), -1)


def test_recall_dataset_well_formed():
    rows = [json.loads(line) for line in _DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) >= 4
    for r in rows:
        assert r["query"] and r["expect"]
        assert r["query_type"] in ("impact", "symbol", "overview", "doc_rule", "general")
    # 至少各有一个 impact / symbol(才能验出 planner 权重对两类的差异)
    types = {r["query_type"] for r in rows}
    assert {"impact", "symbol"} <= types
