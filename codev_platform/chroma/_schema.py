"""chroma daemon —— MCP tool 定义 + 响应 helper (从 server.py 抽出, file-discipline §1)。

纯数据/纯函数: 3 个 Tool 的静态 inputSchema + TextContent 包装 + where 过滤构造。
server.py 的 @server.list_tools() 直接 return tool_definitions()。
"""
from __future__ import annotations

import json
from typing import Any

from mcp.types import TextContent, Tool

from codev_platform.core.errors import ErrorCode, to_mcp_error


CATEGORIES = ["rule", "incident", "tooling_incident", "design", "operations", "claude_md", "skill", "doc", "tool_doc", "memory", "dev_log", "all"]
# 注: module 不再做 schema 硬 enum —— 各 project 已索引的子模块集是逐项目动态值
# (stock-* 项目有 stock-admin-*, codev-platform 有 web-ui 等), 写死 enum 会把合法 module 当非法拒掉
# (2026-06-04 实测: codev-platform 项目 module="web-ui" 被 enum 拒)。改为自由字符串 +
# _build_where 对未知 module 优雅返空(不报错)。常见值见下 (仅文档提示, 非校验白名单);
# 要看某项目真实模块集用 list_collections 的 by_module。
MODULES = ["platform", "stock-admin-api", "stock-admin-web", "stock-pipeline", "web-ui"]


def tool_definitions() -> list[Tool]:
    """3 个 tool 的静态定义 (search_docs / list_collections / get_by_file)。"""
    return [
        Tool(
            name="search_docs",
            description=(
                "语义搜索平台 markdown 文档（规则 / 事故 / 设计 / 运维 / CLAUDE.md / skill）。"
                "返回 top-k 相关 chunk，可按 category / module 精确过滤。"
                "显式过滤后零命中时保留空数组，并追加 filter_miss 诊断。"
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
                        "description": (
                            "按索引路径规则生成的文档类别精确过滤。"
                            "例如 docs/plans 通常是 dev_log，docs/architecture/roadmap 通常是 design。"
                        ),
                    },
                    "module": {
                        "type": "string",
                        "default": "all",
                        "description": (
                            "按文件路径归属的子模块精确过滤，不表示文档内容涉及的业务模块。"
                            "仓库根 docs/** 通常归 platform；自由字符串、逐项目动态值，无效值返空不报错。"
                            "常见: " + ", ".join(MODULES) + "。"
                            "不确定本项目有哪些模块 → 先用 list_collections 看 by_module。"
                        ),
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


def _err(msg: str, code: ErrorCode = ErrorCode.INTERNAL) -> list[TextContent]:
    """MCP 错误体。向后兼容: `error` 字符串保留 (客户端/测试读子串), 并排新增机器可读 `code`。"""
    return [TextContent(type="text", text=json.dumps(to_mcp_error(msg, code), ensure_ascii=False))]


def _ok(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _filter_miss_content(category: str, module: str) -> TextContent | None:
    """显式过滤零命中的兼容诊断；正式结果仍由首个内容块承载。"""
    category = category or "all"
    module = module or "all"
    if category == "all" and module == "all":
        return None

    suggestions: list[dict[str, str]] = []

    def add_suggestion(next_category: str, next_module: str) -> None:
        suggestion = {"category": next_category, "module": next_module}
        if suggestion not in suggestions:
            suggestions.append(suggestion)

    if module != "all":
        add_suggestion(category, "all")
    if category != "all":
        add_suggestion("all", module)
    add_suggestion("all", "all")

    payload = {
        "code": "filter_miss",
        "message": "显式 category/module 过滤后的结果为空；这不等同于索引未同步。",
        "requested_filters": {"category": category, "module": module},
        "suggested_retries": suggestions,
        "hints": [
            "module 按文件路径归属精确匹配；仓库根 docs/** 通常归 platform，而不是内容涉及的子模块。",
            "先用 list_collections 查看 by_category/by_module；已知路径时改用 get_by_file。",
        ],
    }
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))


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
