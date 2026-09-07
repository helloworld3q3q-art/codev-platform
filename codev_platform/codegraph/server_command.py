"""CodeGraph 服务外部可执行入口的固定解析策略。"""

from __future__ import annotations

import os
from pathlib import Path
import sys


_DEFAULT_COMMAND = "codegraph"
_FORBIDDEN_CHARACTERS = frozenset(
    (";", "&", "|", "$", "`", ">", "<", "\n", "\r", "*", "?", "(", ")", '"', "'", " ")
)


def resolve_codegraph_command() -> str:
    """只接受默认裸名或真实绝对文件，其他配置失败关闭到默认值。"""
    raw = os.getenv("CODEGRAPH_CMD")
    if not raw:
        return _DEFAULT_COMMAND
    command = raw.strip()
    if command == _DEFAULT_COMMAND:
        return _DEFAULT_COMMAND
    if any(character in command for character in _FORBIDDEN_CHARACTERS):
        _warn("含非法字符")
        return _DEFAULT_COMMAND
    path = Path(command)
    executable = os.name == "nt" or os.access(path, os.X_OK)
    if path.is_absolute() and path.is_file() and executable:
        return str(path)
    _warn("既非默认裸名也非可执行的绝对路径文件")
    return _DEFAULT_COMMAND


def _warn(reason: str) -> None:
    print(
        f"[codegraph.server] WARN: CODEGRAPH_CMD {reason}，回退默认 'codegraph'",
        file=sys.stderr,
        flush=True,
    )


__all__ = ["resolve_codegraph_command"]
