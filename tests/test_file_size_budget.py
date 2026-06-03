"""静态预算 (refactor-largefile-errorcode plan §A.5 / next-plan D2) —— 防大文件回潮。

`codev_platform/**/*.py` 单文件 ≤600 行 (file-discipline §1 跨模块兜底; 子模块更严的自管)。
白名单 = 当前已超、待后续拆的文件; **新文件不得进白名单**, 超 600 即测试红, 逼当场拆。
白名单条目若已拆到 ≤600 由第二个测试逼其移除 (防白名单僵化)。
"""
from __future__ import annotations

import pathlib

_LIMIT = 600
_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 当前已超、暂缓拆 (各有后续拆分计划)。新增超限文件**严禁**加这里——先拆。
_WHITELIST = {
    "codev_platform/cli.py",                          # CLI 多子命令聚合, 待按子命令分文件
    "codev_platform/plugins/builtin/sql.py",          # SQL 多方言 + Python/Java DML 解析, 待拆
    "codev_platform/plugins/builtin/_stack_scan.py",  # 多栈扫描基座 (react/fastapi/spring...), 待拆
    "codev_platform/cross_link/server.py",            # cross-link MCP server, 退役中 (Track A6 迁 store)
    "codev_platform/ops/reindex.py",                  # reindex 多 stage 编排, 待按 stage 拆
}


def _line_count(p: pathlib.Path) -> int:
    with p.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def test_no_python_file_exceeds_budget():
    offenders = []
    for p in (_ROOT / "codev_platform").rglob("*.py"):
        rel = p.relative_to(_ROOT).as_posix()
        if rel in _WHITELIST:
            continue
        n = _line_count(p)
        if n > _LIMIT:
            offenders.append(f"{rel}: {n} 行")
    assert not offenders, (
        "以下文件超 600 行预算 (拆分, 或显式加白名单并配拆分计划):\n  " + "\n  ".join(offenders)
    )


def test_whitelist_entries_still_oversized():
    """白名单条目若已拆到 ≤600, 必须从白名单移除 (防僵化, 让预算持续收紧)。"""
    stale = []
    for rel in _WHITELIST:
        p = _ROOT / rel
        if not p.exists():
            stale.append(f"{rel}: 文件不存在 (从白名单移除)")
        elif _line_count(p) <= _LIMIT:
            stale.append(f"{rel}: {_line_count(p)} 行已达标 (从白名单移除)")
    assert not stale, "白名单有该移除的条目:\n  " + "\n  ".join(stale)
