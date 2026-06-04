"""agent 运行期请求上下文 — contextvar 传递 (user / org / project / task)。

工具(如 remember)在 run() 内需要当前对话的身份/任务上下文来写 memory, 但这些是
per-request 的、模型不知道(模型 args 里给不出 user_id)。ChatService.ask 每轮设,
loop 同步调用链内工具读得到; 退出即 reset(不跨请求泄漏)。同 chroma server 的
contextvar 多租户路由思路。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass


@dataclass(frozen=True)
class RunContext:
    user_id: str
    org_id: str
    project_id: str | None = None
    task_id: str | None = None       # M1: 当前会话绑定的任务(来自 /chat 请求, None=非任务会话)


_current: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "agent_run_context", default=None
)


def set_run_context(ctx: RunContext):
    """设当前请求上下文, 返回 token 供 reset_run_context。"""
    return _current.set(ctx)


def get_run_context() -> RunContext | None:
    """当前请求上下文; 非 agent 请求路径(无设置)返回 None。"""
    return _current.get()


def reset_run_context(token) -> None:
    """退出请求时还原(配合 set_run_context 的 token, 防跨请求泄漏)。"""
    _current.reset(token)
