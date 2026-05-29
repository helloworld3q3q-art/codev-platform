"""cross-link 工具(in-process via CrossLayerDB).

封 codev_platform.cross_link.query 的常用查询,给 agent 看"表/端点的跨层引用链"。
backend = in-process sqlite(最省,无需起 daemon)。
P2 多租户:按 project_id 路由到 cross_link_db_path(project_id);None 走 .default()(cwd 兼容)。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool


def _open_db(project_id: str | None):
    """按 project_id 开只读 cross_layer DB;None 回退 CrossLayerDB.default()(cwd 推导)。"""
    from codev_platform.cross_link.query import CrossLayerDB
    if not project_id:
        return CrossLayerDB.default()
    from codev_platform.core.paths import cross_link_db_path
    path = cross_link_db_path(project_id)
    if not path.exists():
        raise FileNotFoundError(f"project '{project_id}' 的 cross_layer DB 不存在: {path}")
    return CrossLayerDB(sqlite3.connect(f"file:{path}?mode=ro", uri=True))


class CrossLinkTableRefsTool(Tool):
    name = "cross_link_table_refs"
    description = (
        "查一张数据库表的全部跨层引用:谁读 / 谁写 / 谁更新 / 谁定义(Python / Java / "
        "Flyway)。用于改表结构前看影响面。入参 table=表名(如 stock_recommendation_track)。"
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
        try:
            db = _open_db(self.project_id)
            try:
                refs = db.find_all_references(table)
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001 — 工具边界,异常转结果回灌模型
            return ToolResult(call_id="", content=f"cross_link 查询失败: {e}", is_error=True)
        if not any(refs.values()):
            return ToolResult(call_id="", content=f"表 {table} 无跨层引用记录(或索引未建)。")
        return ToolResult(call_id="", content=json.dumps(refs, ensure_ascii=False, indent=2))


class CrossLinkEndpointTool(Tool):
    name = "cross_link_endpoint_callers"
    description = (
        "查一个 Java 端点被哪些前端 API 调用(endpoint ↔ 前端链路)。"
        "入参 endpoint=Java 端点方法名。"
    )
    input_schema = {
        "type": "object",
        "properties": {"endpoint": {"type": "string", "description": "Java 端点方法名"}},
        "required": ["endpoint"],
    }

    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id

    def run(self, args: dict[str, Any]) -> ToolResult:
        endpoint = (args or {}).get("endpoint", "").strip()
        if not endpoint:
            return ToolResult(call_id="", content="缺少 endpoint 参数", is_error=True)
        try:
            db = _open_db(self.project_id)
            try:
                callers = db.list_endpoint_callers(endpoint)
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001
            return ToolResult(call_id="", content=f"cross_link 查询失败: {e}", is_error=True)
        if not callers:
            return ToolResult(call_id="", content=f"端点 {endpoint} 无前端调用记录。")
        return ToolResult(call_id="", content=json.dumps(callers, ensure_ascii=False, indent=2))


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(CrossLinkTableRefsTool(project_id))
    registry.register(CrossLinkEndpointTool(project_id))
