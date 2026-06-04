"""CLI 子命令共享的基础 IO helper。

cli.py 与所有 cli_cmds.* 共用,单独成模块避免循环 import
(cli.py import 子命令模块, 子命令模块又要用 _print/_eprint)。
"""
from __future__ import annotations

import sys


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
