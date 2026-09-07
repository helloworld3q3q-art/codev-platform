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
    assert len(rows) >= 16
    for r in rows:
        assert r["query"] and r["expect"] and r["project_id"]
        assert r["query_type"] in ("impact", "symbol", "overview", "doc_rule", "general")
    # 至少各有一个 impact / symbol(才能验出 planner 权重对两类的差异)
    types = {r["query_type"] for r in rows}
    assert {"impact", "symbol"} <= types
    # 跨 ≥2 项目防单仓过拟合(run_recall 按 project_id 过滤, 每项目在自己双 lane 内跑 A/B)
    pids = {r["project_id"] for r in rows}
    assert {"codev-platform", "openclaw-stock"} <= pids


def test_run_recall_wires_paired_delta_ci(monkeypatch, tmp_path):
    """run_recall 必须把 paired bootstrap CI 接进 metrics —— mrr_delta_ci95 / ndcg@k_delta_ci95
    各为 [lo, hi] 两元素。monkeypatch 掉双 lane store + recall_code, 在 Windows 即可验接线(不依赖 WSL)。"""
    import codev_platform.core.repos as repos_mod
    import codev_platform.graph.store as store_mod
    import codev_platform.recall as recall_mod
    from eval.suites.recall import run_recall

    f = tmp_path / "store"
    f.write_text("x")
    monkeypatch.setattr(store_mod, "graph_store_path", lambda pid: f)
    monkeypatch.setattr(repos_mod, "project_codegraph_dbs", lambda pid: [f])

    # weighted(planner 自动)把相关项排首位, uniform(等权)排第三 → 命中行得正 delta。
    def fake_recall_code(query, pid, *, weights=None, limit=10, **kw):
        rel = _hit("r", name="weighted_rrf", file="codev_platform/recall/fusion.py")
        noise = [_hit("a", name="x"), _hit("b", name="y")]
        return [rel, *noise] if weights is None else [*noise, rel]
    monkeypatch.setattr(recall_mod, "recall_code", fake_recall_code)

    res = run_recall("codev-platform", k=5)
    assert res["status"] == "ok"
    m = res["metrics"]
    for key in ("mrr_delta_ci95", "ndcg@5_delta_ci95"):
        assert isinstance(m[key], list) and len(m[key]) == 2
        assert m[key][0] <= m[key][1]


def test_recall_dataset_query_text_classifies_to_labeled_type():
    """金标 query 文本必须真的被 planner 关键词分类归到它标的 query_type。

    run_recall 跑时 weights=None → recall_code 内部按 **query 文本** 自动分类决定 lane 加权;
    query_type 字段只是标注。若 query 文本误分(如名字带 "impact" 的符号被归 IMPACT→graph 加权),
    加权 lane 就错, 污染 weighted-vs-uniform A/B 信号。这条把"标的类型 == 真分类"钉死在 Windows
    可跑的纯函数上, 防扩集时引入毒丸(子串匹配 + 平手 IMPACT 优先于 SYMBOL)。
    """
    from codev_platform.agent.planner import classify_query

    rows = [json.loads(line) for line in _DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    mismatched = [(r["query"], r["query_type"], classify_query(r["query"]))
                  for r in rows if classify_query(r["query"]) != r["query_type"]]
    assert not mismatched, f"query 文本真分类 != 标的 query_type: {mismatched}"
