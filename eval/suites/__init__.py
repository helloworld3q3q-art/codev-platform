"""eval suites —— 每个 suite 一模块(单一职责), run_eval(CLI)只做 dispatch + 输出。

各 suite 暴露一个 `run_*(...)` 入口 + 自己的私有 helper。新增 suite = 加一个模块 + 在
run_eval 的 dispatch 表加一行, 核心零改。
"""
from __future__ import annotations

from eval.suites.codegraph import run_codegraph
from eval.suites.code_intelligence import run_code_intelligence
from eval.suites.memory import run_memory
from eval.suites.planner import run_planner
from eval.suites.recall import run_recall
from eval.suites.retrieval import run_retrieval

__all__ = [
    "run_code_intelligence",
    "run_codegraph",
    "run_memory",
    "run_planner",
    "run_recall",
    "run_retrieval",
]
