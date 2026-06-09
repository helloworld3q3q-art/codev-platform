"""planner suite 单测 —— 标准 golden 满分 + 硬集暴露关键词分类上限 (Phase 7 完整版依据)。

纯逻辑, 不调 LLM。硬集准确率 < 标准 = 关键词分类对口语化/无关键词问法的真实差距, 即 LLM
planner 的提升空间。
"""
from __future__ import annotations

from codev_platform.agent.brain import AssistantTurn, LLMProvider
from eval.suites._common import load_jsonl
from eval.suites.planner import run_planner


class _OracleProvider(LLMProvider):
    """理想 LLM: 按数据集返回每个 query 的 gold 标签 —— 验 A/B 管线能正确量出 LLM 提升。"""
    name, model = "oracle", "m"

    def __init__(self) -> None:
        gold = {}
        for f in ("planner.jsonl", "planner_hard.jsonl"):
            for r in load_jsonl(f):
                gold[r["query"]] = r["expect_type"]
        self._gold = gold

    def chat(self, system, messages, tools):
        q = messages[-1].content if messages else ""
        return AssistantTurn(text=self._gold.get(q, "general"), tool_calls=[], stop_reason="end")


def test_standard_golden_full_accuracy():
    rep = run_planner()
    assert rep["status"] == "ok"
    assert rep["metrics"]["classification_accuracy"] == 1.0   # 标准集关键词应满分(回归基线)


def test_hard_set_exposes_keyword_ceiling():
    rep = run_planner()
    # 硬集存在且被单独评分
    assert rep.get("n_hard", 0) >= 10
    hard = rep["metrics"]["classification_accuracy_hard"]
    # 硬集明显低于标准集 —— 关键词分类对对抗/口语化问法有真实差距(LLM planner 的动机)
    assert hard < rep["metrics"]["classification_accuracy"]
    assert hard < 0.6
    # 逐条 details 在场, 可定位误判
    assert len(rep["details_hard"]) == rep["n_hard"]


def test_llm_ab_measures_improvement_over_keyword():
    # provider 注入 → 额外报 LLM 准确率 + 硬集 delta。理想 LLM (oracle) 应把硬集打满 → delta>0。
    rep = run_planner(provider=_OracleProvider())
    m = rep["metrics"]
    assert "classification_accuracy_llm" in m
    assert "classification_accuracy_hard_llm" in m and "classification_accuracy_hard_delta" in m
    assert m["classification_accuracy_hard_llm"] == 1.0          # oracle 硬集满分
    assert m["classification_accuracy_hard_delta"] > 0           # 相对关键词 0.267 有正增益
    # delta 自洽: == llm - keyword
    assert m["classification_accuracy_hard_delta"] == round(
        m["classification_accuracy_hard_llm"] - m["classification_accuracy_hard"], 3)


def test_no_llm_keys_without_provider():
    # 不给 provider → 不产 LLM 指标(默认纯关键词, 处处可跑)。
    m = run_planner()["metrics"]
    assert "classification_accuracy_llm" not in m
    assert "classification_accuracy_hard_llm" not in m


def test_hard_control_cases_still_pass():
    # 硬集里的"控制项"(关键词应命中)必须 ok —— 否则是分类退化而非单纯口语化难度。
    rep = run_planner()
    controls = [d for d in rep["details_hard"]
                if d["query"] in (
                    "改这张表会影响哪些前端页面和接口",
                    "build_default_registry 这个函数在哪个文件定义",
                    "这个项目整体是做什么的",
                    "为什么这里选择 sqlite 而不是 postgres",
                )]
    assert len(controls) == 4
    assert all(c["ok"] for c in controls)
