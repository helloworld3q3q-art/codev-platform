"""planner suite 单测 —— 标准 golden 满分 + 硬集暴露关键词分类上限 (Phase 7 完整版依据)。

纯逻辑, 不调 LLM。硬集准确率 < 标准 = 关键词分类对口语化/无关键词问法的真实差距, 即 LLM
planner 的提升空间。
"""
from __future__ import annotations

from eval.suites.planner import run_planner


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
