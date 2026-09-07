"""测试套件规模下限 (防"测试数静默下降")。

闭合 verification-checklist.md 反复点名的盲区:
  "缺: '测试数不下降'的自动检测 (diff 测试数 < 0 拒绝 merge)"

纯文件扫描 (ast 数 test_ 函数/方法), 不跑 pytest, 跨平台稳定。删测试导致总数跌破
floor 即失败。新增测试后, 把 floor 抬到新的实测值 (留 buffer)。这是地板, 不是精确计数。
"""
from __future__ import annotations

import ast
from pathlib import Path

_TESTS_DIR = Path(__file__).parent

# 当前实测约 1420 个 test_ 函数/方法 (2026-06-10)。floor 取略低于实测, 防误删但不必每次精确同步。
# 大批新增测试后可上调本值锁定新地板。
_FLOOR = 1380


def _count_test_callables() -> int:
    total = 0
    for f in _TESTS_DIR.glob("test_*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                total += 1
    return total


def test_suite_not_shrunk_below_floor():
    n = _count_test_callables()
    assert n >= _FLOOR, (
        f"测试函数数 {n} 跌破地板 {_FLOOR} —— 是否误删了测试? "
        "若为有意精简, 同步下调本文件 _FLOOR 并在 PR 说明原因。"
    )
