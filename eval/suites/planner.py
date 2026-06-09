"""planner suite —— 查询分类准确率 (Phase 7, 纯确定性, 无后端, 处处可跑)。

把 {query -> expect_type} 固化成 golden set 算分类准确率 —— 改分类词表/规则后自动回归。
纯关键词分类, 不调 LLM、不依赖任何后端。
"""
from __future__ import annotations

from eval.metrics import accuracy
from eval.suites._common import load_jsonl


def run_planner() -> dict:
    from codev_platform.agent.planner import classify_query

    rows = load_jsonl("planner.jsonl")
    correct = 0
    details = []
    for r in rows:
        got = classify_query(r["query"])
        ok = got == r["expect_type"]
        correct += 1 if ok else 0
        details.append({"query": r["query"], "expect": r["expect_type"],
                        "got": got, "ok": ok})
    n = len(rows)
    return {
        "suite": "planner",
        "status": "ok",
        "n": n,
        "metrics": {"classification_accuracy": round(accuracy(correct, n), 3)},
        "correct": correct,
        "details": details,
    }
