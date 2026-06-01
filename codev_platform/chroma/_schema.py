"""chroma daemon —— MCP tool 定义 + 响应 helper (从 server.py 抽出, file-discipline §1)。

纯数据/纯函数: 3 个 Tool 的静态 inputSchema + TextContent 包装 + where 过滤构造。
server.py 的 @server.list_tools() 直接 return tool_definitions()。
"""
from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent, Tool


CATEGORIES = ["rule", "incident", "tooling_incident", "design", "operations", "claude_md", "skill", "doc", "tool_doc", "memory", "dev_log", "all"]
MODULES = ["platform", "stock-admin-api", "stock-admin-web", "stock-pipeline", "all"]


def tool_definitions() -> list[Tool]:
    """3 个 tool 的静态定义 (search_docs / list_collections / get_by_file)。"""
    return [
        Tool(
            name="search_docs",
            description=(
                "语义搜索平台 markdown 文档（规则 / 事故 / 设计 / 运维 / CLAUDE.md / skill）。"
                "返回 top-k 相关 chunk，可按 category / module 过滤。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然语言查询"},
                    "k": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                        "description": "返回 chunk 数",
                    },
                    "category": {
                        "type": "string",
                        "enum": CATEGORIES,
                        "default": "all",
                        "description": "文档类别过滤",
                    },
                    "module": {
                        "type": "string",
                        "enum": MODULES,
                        "default": "all",
                        "description": "子模块过滤",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="list_collections",
            description="查看 Chroma 知识库统计（总文档数 / 各 category 数 / 各 module 数）",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_by_file",
            description="按文件路径精确获取该文件的所有 chunks（用于读全文，非语义检索）",
            inputSchema={
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "如 '.claude/rules/pct-sign-convention.md'（相对仓库根路径）",
                    },
                },
                "required": ["file"],
            },
        ),
    ]


def _err(msg: str) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps({"error": msg}, ensure_ascii=False))]


def _ok(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _build_where(category: str, module: str) -> dict | None:
    """构造 Chroma where 过滤（单字段直传，多字段用 $and）。"""
    clauses: list[dict] = []
    if category and category != "all":
        clauses.append({"category": category})
    if module and module != "all":
        clauses.append({"module": module})
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
