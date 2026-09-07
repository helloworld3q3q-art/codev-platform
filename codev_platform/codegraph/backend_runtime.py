"""CodeGraph MCP 子后端的固定启动参数。"""
from __future__ import annotations

import os


def codegraph_backend_environment() -> dict[str, str]:
    """构造关闭 watcher 和共享 daemon 的常态后端环境。

    首次请求仍可能执行追赶同步，因此这些参数不能被解释为只读承诺。
    """
    environment = os.environ.copy()
    environment["CODEGRAPH_NO_WATCH"] = "1"
    environment["CODEGRAPH_NO_DAEMON"] = "1"
    return environment


def codegraph_backend_arguments() -> list[str]:
    """构造显式关闭 watcher 的官方 CodeGraph MCP 参数。"""
    return ["serve", "--mcp", "--no-watch"]


__all__ = ["codegraph_backend_arguments", "codegraph_backend_environment"]
