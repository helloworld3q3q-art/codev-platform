"""依赖注入 — 进程级单例 + provider 解析.

集中在此便于:① 测试时替换(注入假 provider / 空 registry)② 路由层只依赖这些
getter,不自己 new 单例。
"""
from __future__ import annotations

from codev_platform.agent import config as acfg
from codev_platform.agent.brain.base import LLMProvider
from codev_platform.agent.brain.registry import get_provider as _get_provider
from codev_platform.agent.services.chat_service import ChatService
from codev_platform.agent.session import InMemorySessionStore, SessionStore
from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.base import ToolRegistry


def _build_session_store() -> SessionStore:
    """按 config.agent.session_backend 选实现。默认内存;'pg' 等 PG 库就绪后接入(M2)。

    接缝已留:加 SqlSessionStore 后,这里按 backend 分支返回即可,上层零改。
    """
    backend = acfg.get(acfg.agent_cfg(), "agent.session_backend", "memory")
    if backend != "memory":
        # pg 实现(SqlSessionStore)待 codev_platform_memory 库就绪(memory plan M2)
        import sys
        print(f"[agent.deps] session_backend='{backend}' 尚未实现, 回退 memory", file=sys.stderr)
    return InMemorySessionStore()


_sessions: SessionStore = _build_session_store()
_registry: ToolRegistry = build_default_registry()


def get_sessions() -> SessionStore:
    return _sessions


def get_registry() -> ToolRegistry:
    return _registry


def get_provider() -> LLMProvider:
    """每次读 config 解析(便于运行中切 provider). key 缺失抛 RuntimeError."""
    return _get_provider()


_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    """ChatService 单例,注入进程级 sessions/registry + provider 工厂 + max_steps 解析。"""
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService(
            sessions=_sessions,
            registry=_registry,
            provider_factory=_get_provider,
            default_max_steps=lambda: acfg.max_steps(acfg.agent_cfg()),
        )
    return _chat_service
