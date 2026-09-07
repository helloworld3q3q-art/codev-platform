"""planner suite —— 查询分类准确率 (Phase 7, 纯确定性, 无后端, 处处可跑)。

两份 golden:
- `planner.jsonl`(标准): 关键词分类应满分的基线 —— 改词表/规则后自动回归。
- `planner_hard.jsonl`(硬集): 对抗/口语化/无关键词命中的问法 —— **暴露关键词分类的真实上限**,
  为 LLM planner 增强(Phase 7 完整版)提供数据依据(关键词在硬集掉多少 = LLM 的提升空间)。

纯关键词分类, 不调 LLM、不依赖任何后端。LLM planner 的 A/B(关键词 vs LLM 在硬集)需 provider,
是后续 WSL 步, 不在本纯逻辑 suite 内。
"""
from __future__ import annotations

from eval.metrics import accuracy
from eval.suites._common import load_jsonl


def _score(rows: list, classify) -> tuple[int, list]:
    """跑分类器, 返回 (命中数, 逐条 details)。"""
    correct = 0
    details = []
    for r in rows:
        got = classify(r["query"])
        ok = got == r["expect_type"]
        correct += 1 if ok else 0
        details.append({"query": r["query"], "expect": r["expect_type"],
                        "got": got, "ok": ok})
    return correct, details


def run_planner(provider=None) -> dict:
    """关键词分类准确率(标准 + 硬集)。

    provider!=None(由 run_eval --llm 注入配置的 LLMProvider, 需 WSL + key)→ **额外**用
    classify_query_smart(LLM 优先 + 关键词兜底)在标准+硬集打分, 报 LLM 准确率 + 硬集 delta ——
    即 LLM planner 相对纯关键词的真实增益(A/B)。不给 provider = 纯关键词(默认, 处处可跑)。
    """
    from functools import partial

    from codev_platform.agent.planner import classify_query, classify_query_smart

    rows = load_jsonl("planner.jsonl")
    correct, details = _score(rows, classify_query)
    n = len(rows)
    out = {
        "suite": "planner",
        "status": "ok",
        "n": n,
        "metrics": {"classification_accuracy": round(accuracy(correct, n), 3)},
        "correct": correct,
        "details": details,
    }
    # 硬集(若存在): 单独报准确率, 不并进标准基线(两者目的不同 —— 标准钉回归, 硬集量上限)。
    try:
        hard = load_jsonl("planner_hard.jsonl")
    except FileNotFoundError:
        hard = []
    if hard:
        hc, hd = _score(hard, classify_query)
        out["n_hard"] = len(hard)
        out["metrics"]["classification_accuracy_hard"] = round(accuracy(hc, len(hard)), 3)
        out["correct_hard"] = hc
        out["details_hard"] = hd

    # LLM A/B: smart 分类器(LLM 优先 + 关键词兜底) vs 纯关键词。重点看硬集 delta。
    if provider is not None:
        smart = partial(classify_query_smart, provider=provider)
        sc, sd = _score(rows, smart)
        m = out["metrics"]
        m["classification_accuracy_llm"] = round(accuracy(sc, n), 3)
        out["details_llm"] = sd
        if hard:
            shc, shd = _score(hard, smart)
            m["classification_accuracy_hard_llm"] = round(accuracy(shc, len(hard)), 3)
            m["classification_accuracy_hard_delta"] = round(
                m["classification_accuracy_hard_llm"] - m["classification_accuracy_hard"], 3)
            out["details_hard_llm"] = shd
    return out
