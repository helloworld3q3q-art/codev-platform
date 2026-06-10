"""Phase A 抗错误前提 —— 数据集完整性 + bootstrap CI + cross_project 跑全集(纯逻辑, Windows 可跑)。

真跑 agent loop(A3)是 WSL 步(需 provider + 后端); 本文件只钉:
- false_premise / control 金标的**结构红线**(无 must_not / 跨 ≥2 项目 / 每例带可审计的 premise 证据);
- `_bootstrap_ci` 确定性 + 区间语义(Gate A 靠 false vs control 的 CI 重叠否决策);
- `cross_project=True` 不按单 project 过滤(否则 openclaw-stock 例被丢)。
不连模型、不依赖后端。
"""
from __future__ import annotations

import json
from pathlib import Path

from eval.suites._common import token_match
from eval.suites.agent_e2e import _bootstrap_ci, aggregate, run_agent_e2e

_DATASETS = Path("eval/datasets")
_FALSE = "agent_e2e_false_premise.jsonl"
_CONTROL = "agent_e2e_false_premise_control.jsonl"
_CATEGORIES = {"nonexistent_capability", "changed_old_thing", "wrong_attribution"}


def _rows(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (_DATASETS / name).read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- A1 false-premise 数据集红线 ----

def test_false_premise_size_and_two_projects():
    rows = _rows(_FALSE)
    assert 10 <= len(rows) <= 15                       # plan: 10–15 例
    pids = {r["project_id"] for r in rows}
    assert {"codev-platform", "openclaw-stock"} <= pids   # 跨 ≥2 项目防单仓过拟合


def test_false_premise_no_must_not_anywhere():
    # 红线: 禁用 must_not 子串(§三十 已证子串分不清肯定/否定 → 假阳)。
    for r in _rows(_FALSE):
        assert "must_not" not in r, f"false-premise 集禁用 must_not: {r['query']}"


def test_false_premise_every_case_audited():
    # 铁律: 每例 premise 必须真为假 + 留可审计证据(category/premise/verified_false)+ 真 must_mention。
    for r in _rows(_FALSE):
        assert r.get("category") in _CATEGORIES, r["query"]
        assert r.get("premise"), r["query"]
        assert r.get("verified_false"), r["query"]      # 人工核对的"前提为假"证据
        assert r.get("must_mention"), r["query"]        # 靠真机制判, 不靠负样本


def test_false_premise_covers_three_categories():
    cats = {r["category"] for r in _rows(_FALSE)}
    assert cats == _CATEGORIES                           # ①不存在能力 ②已删/改旧物 ③错误归因 三类齐全


# ---- A2 对照组: 同实体真前提, 锚点镜像 ----

def test_control_mirrors_false_premise():
    false_rows, ctrl_rows = _rows(_FALSE), _rows(_CONTROL)
    assert len(ctrl_rows) == len(false_rows)            # 1:1 孪生
    for r in ctrl_rows:
        assert "must_not" not in r
        assert r.get("must_mention")
        assert r.get("category") == "control"
    # 对照组锚点全集应等于 false 集锚点全集(隔离"前提为假"单一变量, 同实体)。
    f_anchors = {a for r in false_rows for a in r["must_mention"]}
    c_anchors = {a for r in ctrl_rows for a in r["must_mention"]}
    assert f_anchors == c_anchors


def test_anchors_are_real_tokens():
    # 锚点应是能被 token_match 命中的真 token(防写成永不命中的怪串)。
    for name in (_FALSE, _CONTROL):
        for r in _rows(name):
            for a in r["must_mention"]:
                assert token_match(f" {a} ", a) is True, (name, a)


# ---- A3: bootstrap CI(Gate A 比组靠它)----

def test_bootstrap_ci_deterministic():
    vals = [1.0, 0.5, 0.0, 1.0, 0.5, 0.0]
    assert _bootstrap_ci(vals) == _bootstrap_ci(vals)   # 固定 seed → 完全可复现


def test_bootstrap_ci_edge_cases():
    assert _bootstrap_ci([]) is None
    assert _bootstrap_ci([0.7]) == [0.7, 0.7]            # 单点退化为点区间
    assert _bootstrap_ci([0.5, 0.5, 0.5]) == [0.5, 0.5]  # 零方差 → 宽 0


def test_bootstrap_ci_within_unit_and_brackets_mean():
    vals = [1.0, 0.8, 0.9, 1.0, 0.7]
    lo, hi = _bootstrap_ci(vals)
    mean = sum(vals) / len(vals)
    assert 0.0 <= lo <= mean <= hi <= 1.0               # 区间含均值且落在 [0,1]


def test_bootstrap_ci_separates_high_from_low():
    # Gate A 判据演练: 明显低的 false 组 vs 明显高的 control 组 → CI 不重叠。
    low = _bootstrap_ci([0.0, 0.0, 0.2, 0.0, 0.1])
    high = _bootstrap_ci([1.0, 1.0, 0.9, 1.0, 0.8])
    assert low[1] < high[0]                              # 上界 < 下界 = CI 不重叠 → 确证差异


def test_aggregate_reports_ci_and_n():
    details = [
        {"grounding_coverage": 1.0, "hallucinated": [], "tool_appropriate": True, "within_budget": True},
        {"grounding_coverage": 0.0, "hallucinated": [], "tool_appropriate": True, "within_budget": True},
    ]
    agg = aggregate(details)
    assert agg["grounding_n"] == 2
    assert isinstance(agg["grounding_ci95"], list) and len(agg["grounding_ci95"]) == 2


# ---- cross_project: 不按单 project 过滤 ----

def test_cross_project_runs_full_set_not_single_project():
    full = _rows(_FALSE)
    # cross_project=True → openclaw-stock 例不被丢, n = 全集。
    rep = run_agent_e2e("codev-platform", provider=None,
                        dataset=_FALSE, cross_project=True)
    assert rep["status"] == "skipped" and rep["n"] == len(full)


def test_default_filter_drops_other_project():
    # 不开 cross_project → 退回单 project 过滤(只剩 codev-platform 例), 反证开关有效。
    cp_only = [r for r in _rows(_FALSE) if r["project_id"] == "codev-platform"]
    rep = run_agent_e2e("codev-platform", provider=None, dataset=_FALSE)
    assert rep["n"] == len(cp_only) < len(_rows(_FALSE))
