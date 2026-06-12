"""影响分析工具 (Track A4) —— 给 agent 看统一图谱的跨层影响链。

吃 A1 桥接后**连通**的 graph store (graph/impact.py 引擎),是 cross_link 工具的统一图谱
替代:回答 README 核心卖点"改一处 → 跨层影响清单"。backend = in-process 只读 sqlite
(data/graph_store/<pid>.sqlite),无 daemon。project_id 给定按项目路由;None 走 cwd 推导。

节点引用 (nodeRef/table/page/endpoint) 既可是 store 节点 id, 也可是 name (表名/端点名/函数名);
name 多同名时返回 ambiguous 候选, 让 agent 用 id 消歧。
"""
from __future__ import annotations

import json
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools._project import resolve_project_id
from codev_platform.agent.tools.base import Tool
from codev_platform.graph import impact as I


def _open_store_ro(project_id: str | None):
    """开只读统一图谱 store; 返回 (store, 实际 project_id)。不存在/读不动抛 FileNotFoundError。
    None 经 resolve_project_id 解析(token 模式禁 cwd fallback, 与 fs/search_docs 同一守卫)。"""
    from codev_platform.graph.store import GraphStoreUnreadable, open_store
    pid = resolve_project_id(project_id)
    try:
        return open_store(pid, mode="ro"), pid
    except GraphStoreUnreadable as exc:
        raise FileNotFoundError(
            f"project '{pid}' 的统一图谱 store 不存在或读不动: {exc} (先跑 reindex --ingest)") from exc


def _run_query(project_id: str | None, fn, *args) -> ToolResult:
    """共用: 开 store -> 调引擎 -> JSON 结果 (异常/未命中转结果回灌模型)。"""
    try:
        store, pid = _open_store_ro(project_id)
    except Exception as e:  # noqa: BLE001 — 工具边界
        return ToolResult(call_id="", content=f"统一图谱 store 不可用: {e}", is_error=True)
    try:
        r = fn(store, pid, *args)
    except Exception as e:  # noqa: BLE001
        return ToolResult(call_id="", content=f"影响分析查询失败: {e}", is_error=True)
    finally:
        store.close()
    # 紧凑 JSON(去缩进/分隔空格): tool-result 计入 miss, 缩进纯格式零信息 → 压扁省 token, grounding 不变(免费 win)。
    return ToolResult(call_id="", content=json.dumps(r, ensure_ascii=False, separators=(",", ":")))


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
        "入参 table=表名 (大小写不敏感)。"
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


class ImpactPathsTool(Tool):
    name = "impact_paths"
    description = (
        "改某节点 → top-N 最强依赖路径:谁经哪几跳依赖它, 每跳带 src/置信/确定性 (可解释)。"
        "比 impact_analysis 的扁平清单多了「逐跳路径链 + 评分排序 + 每跳可信度」, 想看具体怎么依赖、"
        "哪条链最该担心时用它。入参 nodeRef=节点 id 或 name (表名 / 端点名 / 函数名)。"
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
        return _run_query(self.project_id, I.find_impact_paths, ref)


class ContractDriftTool(Tool):
    name = "contract_drift"
    description = (
        "契约漂移自检: 列出悬空前端调用 —— 前端调了后端不暴露的接口(接口被删/改签名/operationId 漂移/"
        "URL 写错)。前后端分离 / 多服务 / 多仓项目改后端接口后查这个, 确认没留断头调用。无入参。"
    )
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        from codev_platform.graph import contract_drift as _cd
        return _run_query(self.project_id, _cd.find_contract_drift)


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(ImpactAnalysisTool(project_id))
    registry.register(TableUsageTool(project_id))
    registry.register(PageDependenciesTool(project_id))
    registry.register(ApiCallersTool(project_id))
    registry.register(ImpactPathsTool(project_id))
    registry.register(ContractDriftTool(project_id))
