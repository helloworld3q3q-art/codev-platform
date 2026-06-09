"""agent_e2e suite 单测 —— 确定性打分器(纯函数)+ skip 语义。

跑真 loop 是 WSL 步(需 provider + 后端); 这里只钉 score_case/aggregate 逻辑 + 无 provider 优雅 skip,
不连模型、不依赖后端。
"""
from __future__ import annotations

from eval.suites.agent_e2e import aggregate, run_agent_e2e, score_case


def test_score_full_coverage_clean():
    case = {"must_mention": ["audit_all_stores", "cli.py"], "expect_tools": ["code_recall"]}
    sc = score_case(case, "audit_all_stores 在 cli.py 的 graph audit 用到", ["code_recall"], 1, budget=12)
    assert sc["grounding_coverage"] == 1.0
    assert sc["missing_mentions"] == [] and sc["hallucinated"] == []
    assert sc["tool_appropriate"] is True and sc["within_budget"] is True


def test_score_partial_coverage():
    case = {"must_mention": ["a_sym", "b_sym", "c_sym"]}
    sc = score_case(case, "只提到 a_sym", [], 0)
    assert sc["grounding_coverage"] == round(1 / 3, 3)
    assert set(sc["missing_mentions"]) == {"b_sym", "c_sym"}


def test_score_hallucination_flagged():
    case = {"must_mention": ["x"], "must_not": ["open_store"]}
    sc = score_case(case, "x 经 open_store 打开", [], 0)
    assert sc["hallucinated"] == ["open_store"]


def test_score_tool_inappropriate():
    case = {"must_mention": ["x"], "expect_tools": ["impact_analysis"]}
    sc = score_case(case, "x", ["read_file"], 1)   # 没用到任何期望工具类
    assert sc["tool_appropriate"] is False


def test_score_over_budget():
    sc = score_case({"must_mention": []}, "ans", ["t"], 20, budget=12)
    assert sc["within_budget"] is False
    assert sc["grounding_coverage"] == 1.0   # 无 must_mention → 满分


def test_score_case_insensitive_match():
    sc = score_case({"must_mention": ["AuditAllStores"]}, "答案提到 auditallstores", [], 0)
    assert sc["grounding_coverage"] == 1.0


def test_aggregate_rates():
    details = [
        {"grounding_coverage": 1.0, "hallucinated": [], "tool_appropriate": True, "within_budget": True},
        {"grounding_coverage": 0.0, "hallucinated": ["bad"], "tool_appropriate": False, "within_budget": True},
    ]
    agg = aggregate(details)
    assert agg["grounding_coverage"] == 0.5
    assert agg["hallucination_rate"] == 0.5
    assert agg["tool_appropriate_rate"] == 0.5
    assert agg["within_budget_rate"] == 1.0


def test_run_skips_without_provider():
    rep = run_agent_e2e("codev-platform", provider=None)
    assert rep["status"] == "skipped" and rep["n"] >= 1   # 有用例但无 provider → skip


def test_run_skips_unknown_project():
    rep = run_agent_e2e("no-such-project", provider=None)
    assert rep["status"] == "skipped"
