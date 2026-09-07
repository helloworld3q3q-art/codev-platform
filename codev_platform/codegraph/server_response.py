"""CodeGraph 多仓响应的纯转换与聚合。"""

from __future__ import annotations

import json

from mcp.types import TextContent


def repo_header(label: str) -> TextContent:
    """为普通文本聚合生成不含仓路径的稳定分隔头。"""
    return TextContent(
        type="text",
        text=json.dumps({"repo": label, "separator": True}, ensure_ascii=False),
    )


def split_platform_args(args: dict | None) -> tuple[dict, bool]:
    """移除平台聚合参数，避免把未知字段转发给外部 CodeGraph。"""
    clean = dict(args or {})
    mode = str(clean.pop("_codev_merge", "") or "").lower()
    return clean, mode in {"json", "structured"}


def content_to_json(content: object) -> dict:
    """把 MCP 内容转换为结构化聚合可编码的最小视图。"""
    if hasattr(content, "model_dump"):
        return content.model_dump(mode="json")
    result = {"type": getattr(content, "type", None)}
    if hasattr(content, "text"):
        result["text"] = content.text
    return result


def structured_response(
    project_id: str | None,
    tool: str,
    repos: list[dict],
    failures: list[dict],
) -> list[TextContent]:
    """生成版本固定的多仓 JSON 聚合响应。"""
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "project_id": project_id,
                    "tool": tool,
                    "merge": "codev-fanout-v1",
                    "repos": repos,
                    "failures": failures,
                },
                ensure_ascii=False,
            ),
        )
    ]


__all__ = ["content_to_json", "repo_header", "split_platform_args", "structured_response"]
