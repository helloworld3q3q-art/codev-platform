"""Tool 抽象 + 注册表.

Tool 对 loop 暴露 name/description/input_schema/run;backend(in-process / daemon /
MCP)封在各 Tool 内部。provider 适配器从 tool_specs() 拿中性 spec 自行转原生格式。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from codev_platform.agent.brain import ToolResult


class Tool(ABC):
    name: str = ""
    description: str = ""
    # JSON Schema (object). 转给各 provider 的 tool spec。
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    @abstractmethod
    def run(self, args: dict[str, Any]) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    """进程内工具注册表."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("tool.name 不能为空")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict[str, Any]]:
        """中性 tool spec 列表,交给 provider 适配器转原生格式."""
        return [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in self._tools.values()
        ]
