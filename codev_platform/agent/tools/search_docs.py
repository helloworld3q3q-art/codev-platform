"""search_docs 工具(SSE MCP client → 已在跑的 chroma daemon).

不在 agent 进程内加载 embedding 模型(省 GPU),而是连 daemon(端口默认 18083)复用暖模型。
多租户:project_id 作 SSE query 参数,daemon 据此路由到正确 collection。
daemon 没起 → 优雅报错(让模型知道检索不可用,不编)。
"""
from __future__ import annotations

import asyncio
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool

_TIMEOUT_SEC = 60


def _daemon_url() -> str:
    from codev_platform.core.config import get, load_config
    port = get(load_config(), "daemon.port", 18083)
    return f"http://127.0.0.1:{port}/sse"


def _resolve_project_id(explicit: str | None) -> str:
    from codev_platform.agent.tools._project import resolve_project_id
    return resolve_project_id(explicit)


async def _call_search(query: str, category: str | None, module: str | None, project_id: str) -> str:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    url = f"{_daemon_url()}?project_id={project_id}"
    args: dict[str, Any] = {"query": query}
    if category:
        args["category"] = category
    if module:
        args["module"] = module

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("search_docs", args)
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts) if parts else "(search_docs 无返回)"


class SearchDocsTool(Tool):
    name = "search_docs"
    description = (
        "语义检索规则 / 设计文档 / 事故复盘 / 操作手册。问'X 的规则在哪 / 怎么做 Y'时用。"
        "入参 query=自然语言查询;可选 category(rule/design/dev_log/...)、module(子模块名)。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "自然语言查询"},
            "category": {"type": "string", "description": "可选:rule / design / dev_log / incident 等"},
            "module": {"type": "string", "description": "可选:子模块名过滤"},
        },
        "required": ["query"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        q = (args or {}).get("query", "").strip()
        if not q:
            return ToolResult(call_id="", content="缺少 query 参数", is_error=True)
        try:
            pid = _resolve_project_id(self.project_id)
            text = asyncio.run(
                asyncio.wait_for(
                    _call_search(q, (args or {}).get("category"), (args or {}).get("module"), pid),
                    timeout=_TIMEOUT_SEC,
                )
            )
        except Exception as e:  # noqa: BLE001 — daemon 没起 / 超时 / 协议错都转结果
            return ToolResult(
                call_id="",
                content=f"search_docs 不可用({type(e).__name__}: {e})。"
                        f"可能 chroma daemon 未运行;改用其它工具或如实告知检索不可用。",
                is_error=True,
            )
        return ToolResult(call_id="", content=text)


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(SearchDocsTool(project_id))
