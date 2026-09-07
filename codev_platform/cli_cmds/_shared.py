"""CLI 子命令共享的基础 IO helper。

cli.py 与所有 cli_cmds.* 共用,单独成模块避免循环 import
(cli.py import 子命令模块, 子命令模块又要用 _print/_eprint)。
"""
from __future__ import annotations

import sys


def _ensure_utf8(stream) -> None:
    """尽力把输出流切 UTF-8 + errors=replace —— 让 CLI 的 emoji / 中文 markdown
    (graph audit / soft-quality 等)在 **Windows 默认 GBK 控制台**也不崩(高可用)。

    reconfigure 不可用(流已重定向 / capsys 替换 / 旧 Py)→ 静默跳过, 由 replace 兜底
    退化为 '?' 而非抛 UnicodeEncodeError。一次性 best-effort, 无全局其它副作用。
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


_ensure_utf8(sys.stdout)
_ensure_utf8(sys.stderr)


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
