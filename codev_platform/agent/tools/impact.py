"""影响分析工具 (Track A4) —— 给 agent 看统一图谱的跨层影响链。

吃 A1 桥接后**连通**的 graph store (graph/impact.py 引擎),是 cross_link 工具的统一图谱
替代:回答 README 核心卖点"改一处 → 跨层影响清单"。backend = in-process 只读 sqlite
(data/graph_store/<pid>.sqlite),无 daemon。project_id 给定按项目路由;None 走 cwd 推导。

节点引用 (nodeRef/table/page/endpoint) 既可是 store 节点 id, 也可是 name (表名/端点名/函数名);
name 多同名时返回 ambiguous 候选, 让 agent 用 id 消歧。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool
from codev_platform.core.project_id import resolve_local
from codev_platform.graph import impact as I


def _open_store_ro(project_id: str | None) -> tuple[sqlite3.Connection, str]:
    """开只读统一图谱 store; 返回 (conn, 实际 project_id)。缺失抛 FileNotFoundError。"""
    from codev_platform.graph.store import graph_store_path
    pid = project_id or resolve_local()
    path = graph_store_path(pid)
    if not path.exists():
        raise FileNotFoundError(f"project '{pid}' 的统一图谱 store 不存在: {path} (先跑 reindex --ingest)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True), pid


def _run_query(project_id: str | None, fn, *args) -> ToolResult:
    """共用: 开 store -> 调引擎 -> JSON 结果 (异常/未命中转结果回灌模型)。"""
    try:
        conn, pid = _open_store_ro(project_id)
    except Exception as e:  # noqa: BLE001 — 工具边界
        return ToolResult(call_id="", content=f"统一图谱 store 不可用: {e}", is_error=True)
    try:
        r = fn(conn, pid, *args)
    except Exception as e:  # noqa: BLE001
        return ToolResult(call_id="", content=f"影响分析查询失败: {e}", is_error=True)
    finally:
        conn.close()
    return ToolResult(call_id="", content=json.dumps(r, ensure_ascii=False, indent=2))


class ImpactAnalysisTool(Tool):
    name = "impact_analysis"
    description = (
        "改某个节点 (表 / 端点 / 后端函数 / 前端调用) 会跨层波及谁:返回按层 (frontend/backend/"
        "database) 分组的受影响清单 + 风险等级 + 可读摘要。改表结构 / 改接口前看影响面用它。"
        "入参 nodeRef=节点 id 或 name (如表名 users、端点名)。"
    )
    input_schema = {
        "type": "object",
        "properties": {"nodeRef": {"type": "string", "description": "节点 id 或 name"}},
        "required": ["nodeRef"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        ref = (args or {}).get("nodeRef", "").strip()
        if not ref:
            return ToolResult(call_id="", content="缺少 nodeRef 参数", is_error=True)
        return _run_query(self.project_id, I.generate_impact_report, ref)


class TableUsageTool(Tool):
    name = "table_usage"
    description = (
        "查一张数据库表被谁使用:哪些后端函数读/写它、哪些端点、哪些前端 (跨层反向链路)。"
        "入参 table=表名 (大小写不敏感)。统一图谱版, 替代 cross_link_table_refs。"
    )
    input_schema = {
        "type": "object",
        "properties": {"table": {"type": "string", "description": "数据库表名"}},
        "required": ["table"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        table = (args or {}).get("table", "").strip()
        if not table:
            return ToolResult(call_id="", content="缺少 table 参数", is_error=True)
        return _run_query(self.project_id, I.find_table_usage, table)


class PageDependenciesTool(Tool):
    name = "page_dependencies"
    description = (
        "查一个前端页/组件依赖的下游:它调哪些端点、经哪些后端函数、最终碰哪些表 (正向链路)。"
        "入参 pageRef=前端节点 id 或 name。"
    )
    input_schema = {
        "type": "object",
        "properties": {"pageRef": {"type": "string", "description": "前端节点 id 或 name"}},
        "required": ["pageRef"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        ref = (args or {}).get("pageRef", "").strip()
        if not ref:
            return ToolResult(call_id="", content="缺少 pageRef 参数", is_error=True)
        return _run_query(self.project_id, I.find_page_dependencies, ref)


class ApiCallersTool(Tool):
    name = "api_callers"
    description = (
        "查一个后端端点被哪些前端调用 (endpoint ↔ 前端链路)。入参 endpointRef=端点 id 或 name。"
        "统一图谱版, 替代 cross_link_endpoint_callers。"
    )
    input_schema = {
        "type": "object",
        "properties": {"endpointRef": {"type": "string", "description": "端点 id 或 name"}},
        "required": ["endpointRef"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        ref = (args or {}).get("endpointRef", "").strip()
        if not ref:
            return ToolResult(call_id="", content="缺少 endpointRef 参数", is_error=True)
        return _run_query(self.project_id, I.find_api_callers, ref)


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(ImpactAnalysisTool(project_id))
    registry.register(TableUsageTool(project_id))
    registry.register(PageDependenciesTool(project_id))
    registry.register(ApiCallersTool(project_id))
