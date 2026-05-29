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
    """按 config.agent.session_backend 选实现:'memory'(默认,进程内)| 'pg'(持久化)。
    上层(ChatService)只依赖 SessionStore 抽象,换实现零改。
    """
    cfg = acfg.agent_cfg()
    backend = acfg.get(cfg, "agent.session_backend", "memory")
    if backend == "pg":
        import sys
        dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if not dsn:
            print("[agent.deps] session_backend=pg 但 memory.pg_dsn 未配, 回退 memory", file=sys.stderr)
            return InMemorySessionStore()
        try:
            from codev_platform.agent.session_pg import SqlSessionStore
            return SqlSessionStore(dsn)  # schema 首次操作时幂等建;连不上在首次操作报
        except Exception as e:  # noqa: BLE001 — 缺 psycopg / DSN 坏 → 回退内存,不挂服务
            print(f"[agent.deps] PG 会话存储不可用({type(e).__name__}: {e}), 回退 memory", file=sys.stderr)
            return InMemorySessionStore()
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
            registry_factory=build_default_registry,  # (project_id) -> registry,P2 多租户
            provider_factory=_get_provider,
            default_max_steps=lambda: acfg.max_steps(acfg.agent_cfg()),
        )
    return _chat_service
