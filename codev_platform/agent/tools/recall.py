"""跨 lane 代码召回工具 (Phase 6) —— 给 agent 一个"找相关代码"的统一入口。

融合 graph(架构/跨层节点)+ codegraph(符号 FTS)→ 按 query 类型自动调权、来源可解释的
统一排名。让 agent 一次拿到"跟 X 最相关的代码实体", 不必分别调 codegraph 搜符号 + 查图谱
再人脑合并。backend = recall_code(in-process 直读本地 sqlite, 免 daemon, 每 lane fail-soft);
project_id 给定按项目路由, None 走 cwd 推导。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool

_DEFAULT_LIMIT = 12
_MAX_LIMIT = 50


class CodeRecallTool(Tool):
    name = "code_recall"
    description = (
        "跨 lane 代码召回:给一个检索词, 一次拿最相关的代码实体 —— 融合 graph(架构/跨层节点)"
        "+ codegraph(符号定义/调用)并按 query 类型自动调权, 每条标出来自哪个 lane。想知道"
        "'哪些代码跟 X 相关 / 某功能在哪实现 / 找某符号'时**先用它**, 比分别调 codegraph 搜 + "
        "查图谱再合并更省。入参 query=检索词(支持多词); limit 可选(默认 12)。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索词(支持多词, 如 'weighted rrf fusion')"},
            "limit": {"type": "integer", "description": "返回上限(默认 12, 上限 50)"},
        },
        "required": ["query"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        query = (args or {}).get("query", "").strip()
        if not query:
            return ToolResult(call_id="", content="缺少 query 参数", is_error=True)
        limit = max(1, min(int((args or {}).get("limit") or _DEFAULT_LIMIT), _MAX_LIMIT))
        try:
            # None 经 resolve_project_id 解析(token 模式禁 cwd fallback, 与 fs/search_docs 同一守卫)
            from codev_platform.agent.tools._project import resolve_project_id
            pid = resolve_project_id(self.project_id)
            from codev_platform.recall import recall_code
            hits = recall_code(query, pid, limit=limit)
        except Exception as e:  # noqa: BLE001 — 工具边界: 异常转结果回灌模型, 不崩 loop
            return ToolResult(call_id="", content=f"代码召回失败: {e}", is_error=True)
        out = {
            "query": query,
            "count": len(hits),
            "lanes": sorted({lane for h in hits for lane in h.lanes}),
            "hits": [asdict(h) for h in hits],
        }
        # 紧凑 JSON(去缩进/分隔空格): tool-result 每步计入 miss, 缩进是纯格式零信息 → 压扁省
        # ~15-20% 该工具 token, grounding 不受影响(免费 win, 2026-06-11 成本面板; impact/codegraph 同)。
        return ToolResult(call_id="", content=json.dumps(out, ensure_ascii=False, separators=(",", ":")))


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(CodeRecallTool(project_id))
