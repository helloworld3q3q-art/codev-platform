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


def run_planner() -> dict:
    from codev_platform.agent.planner import classify_query

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
    return out
