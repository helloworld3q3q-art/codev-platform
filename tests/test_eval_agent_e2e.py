"""agent_e2e suite 单测 —— 确定性打分器(纯函数)+ skip 语义。

跑真 loop 是 WSL 步(需 provider + 后端); 这里只钉 score_case/aggregate 逻辑 + 无 provider 优雅 skip,
不连模型、不依赖后端。
"""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider
from eval.suites.agent_e2e import (
    _parse_score,
    aggregate,
    judge_answer,
    run_agent_e2e,
    run_planner_e2e_ab,
    score_case,
)


class _TextProvider(LLMProvider):
    name, model = "fake", "m"

    def __init__(self, text):
        self._text = text

    def chat(self, system, messages, tools):
        return AssistantTurn(text=self._text, tool_calls=[], stop_reason="end")


class _BoomProvider(LLMProvider):
    name, model = "boom", "m"

    def chat(self, system, messages, tools):
        raise RuntimeError("down")


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


# ---- E3 judge ----

def test_parse_score():
    assert _parse_score("4") == 4
    assert _parse_score("评分: 5 分") == 5
    assert _parse_score("无法判断") is None
    assert _parse_score(None) is None
    assert _parse_score("9") is None          # 超 1-5 范围(只认 1-5 字符)


def test_judge_answer_valid_and_fallback():
    case = {"query": "q", "rubric": "r"}
    assert judge_answer(case, "ans", _TextProvider("4")) == 4
    assert judge_answer(case, "ans", _TextProvider("garbage")) is None
    assert judge_answer(case, "ans", _BoomProvider()) is None   # provider 故障 → None
    assert judge_answer(case, "ans", None) is None


def test_aggregate_includes_judge_when_present():
    details = [
        {"grounding_coverage": 1.0, "hallucinated": [], "tool_appropriate": True,
         "within_budget": True, "judge_score": 5},
        {"grounding_coverage": 1.0, "hallucinated": [], "tool_appropriate": True,
         "within_budget": True, "judge_score": 3},
    ]
    agg = aggregate(details)
    assert agg["judge_score_avg"] == 4.0 and agg["judged_n"] == 2


def test_aggregate_no_judge_key_when_absent():
    details = [{"grounding_coverage": 1.0, "hallucinated": [], "tool_appropriate": True,
                "within_budget": True}]
    assert "judge_score_avg" not in aggregate(details)


# ---- E4 planner A/B ----

def test_planner_ab_three_variants_skip_without_provider():
    rep = run_planner_e2e_ab("codev-platform", provider=None)
    assert rep["status"] == "skipped"
    assert set(rep["variants"]) == {"off", "keyword", "llm"}   # 3 变体都在
    assert rep["n"] >= 1 and "reason" in rep                   # _print_human 安全


def test_dataset_param_loads_hard_set():
    # dataset 参数应切到硬集(6 case); 无 provider → skip 但 n 反映硬集规模。
    rep = run_agent_e2e("codev-platform", provider=None, dataset="agent_e2e_hard.jsonl")
    assert rep["status"] == "skipped" and rep["n"] == 6
    ab = run_planner_e2e_ab("codev-platform", provider=None, dataset="agent_e2e_hard.jsonl")
    assert ab["n"] == 6
