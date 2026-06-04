"""remember 工具 — agent 把任务记忆写进分层 memory (M1 写侧闭环)。

对话中模型判断"这是值得跨轮/跨会话记住的任务目标/约束/决策/阻塞/验收"时调本工具,
写进 PG memory_entries(带当前 RunContext 的 user/org/project/task)。读侧召回早已有,
本工具补上写侧 —— agent 对话从"只读不沉淀"变成闭环。

身份/项目/任务不靠模型给(它不知道 user_id), 从 runctx(ChatService.ask 设)拿。
"""
from __future__ import annotations

from typing import Any

from codev_platform.agent.brain import ToolResult
from codev_platform.agent.tools.base import Tool


class RememberTool(Tool):
    name = "remember"
    description = (
        "把当前任务值得跨轮/跨会话记住的信息(目标/约束/决策/已做/阻塞/验收标准)写进长期记忆, "
        "供后续对话召回。判断'这条信息以后还需要'时调;一次一条、简洁。"
        "入参 content=要记的内容;可选 kind(task 默认/fact/preference)、"
        "task_state(active/blocked/done, 仅记录任务进展时给)。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "要记住的内容(简洁一条)"},
            "kind": {"type": "string", "description": "可选: task(默认) / fact / preference"},
            "task_state": {"type": "string", "description": "可选: active / blocked / done"},
        },
        "required": ["content"],
    }

    def run(self, args: dict[str, Any]) -> ToolResult:
        args = args or {}  # 统一防 None: 后续 args.get(kind/task_state) 也安全
        content = args.get("content", "").strip()
        if not content:
            return ToolResult(call_id="", content="缺少 content 参数", is_error=True)
        # lazy import 破循环(deps -> tools/__init__ -> remember -> deps)
        from codev_platform.agent import deps
        from codev_platform.agent.memory_store import MemoryEntry
        from codev_platform.agent.runctx import get_run_context

        ctx = get_run_context()
        if ctx is None:
            return ToolResult(
                call_id="", content="无运行上下文, 无法确定记忆归属(身份/项目)", is_error=True
            )
        store = deps.get_memory_store()
        if store is None:
            return ToolResult(
                call_id="", content="memory 未启用(未配 memory.pg_dsn), 无法记忆", is_error=True
            )

        # 有 project 记 project 作用域(团队可共享), 否则记 personal。
        if ctx.project_id:
            scope, scope_ref = "project", ctx.project_id
        else:
            scope, scope_ref = "personal", ctx.user_id
        entry = MemoryEntry(
            id="", scope=scope, scope_ref=scope_ref, owner_user_id=ctx.user_id,
            org_id=ctx.org_id, content=content,
            kind=(args.get("kind") or "task"),
            task_id=ctx.task_id, task_state=args.get("task_state"),
        )
        try:
            eid = store.write(entry)
        except Exception as e:  # noqa: BLE001 — 写库失败转结果, 不崩 loop
            return ToolResult(call_id="", content=f"记忆写入失败({type(e).__name__}: {e})", is_error=True)
        tail = f", task={ctx.task_id}" if ctx.task_id else ""
        return ToolResult(call_id="", content=f"已记住(scope={scope}{tail}, id={eid[:8]})")


def register_into(registry, project_id: str | None = None) -> None:
    registry.register(RememberTool())
