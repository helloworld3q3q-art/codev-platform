"""依赖注入 — 进程级单例 + provider 解析.

集中在此便于:① 测试时替换(注入假 provider / 空 registry)② 路由层只依赖这些
getter,不自己 new 单例。
"""
from __future__ import annotations

from codev_platform.agent import config as acfg
from codev_platform.agent.brain.base import LLMProvider
from codev_platform.agent.brain.registry import get_provider as _get_provider
from codev_platform.agent.brain.registry import loop_policy as _loop_policy
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
        # 严格运行时模式(默认 False=dev 友好):backend=pg 但 DSN 缺 / 构造失败时,
        # strict=True → raise 让启动暴露(生产 fail-loud,与 config #6 同思路);
        # strict=False → 维持回退 InMemory + 警告(dev 不变)。
        strict = acfg.get(cfg, "agent.strict_runtime", False)
        dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if not dsn:
            if strict:
                raise RuntimeError(
                    "session_backend=pg 但 memory.pg_dsn / CODEV_PLATFORM_MEMORY_DSN 未配; "
                    "agent.strict_runtime=true 拒绝静默回退 InMemory(生产会话不持久)。"
                    "请配置 DSN 或设 agent.strict_runtime=false 走 dev 回退。"
                )
            print("[agent.deps] session_backend=pg 但 memory.pg_dsn 未配, 回退 memory", file=sys.stderr)
            return InMemorySessionStore()
        # 读写分离扩展口(预留):配了只读副本 DSN 则读走它,否则读写同库
        read_dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
        pool_max = acfg.get(cfg, "memory.pool_max_size", 10)
        try:
            from codev_platform.agent.session_pg import SqlSessionStore
            return SqlSessionStore(dsn, read_dsn=read_dsn, max_size=pool_max)  # schema 首次操作幂等建
        except Exception as e:  # noqa: BLE001 — 缺 psycopg / DSN 坏 → 回退内存,不挂服务
            if strict:
                raise RuntimeError(
                    f"session_backend=pg 且 agent.strict_runtime=true,但 PG 会话存储构造失败"
                    f"({type(e).__name__}: {e}); 拒绝静默回退 InMemory。"
                    f"请修复 DSN / 安装 psycopg,或设 agent.strict_runtime=false 走 dev 回退。"
                ) from e
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


_memory_store_built = False
_memory_store = None  # type: ignore[var-annotated]


def get_memory_store():
    """记忆条目存储(SqlMemoryStore)。未配 memory.pg_dsn / 缺 psycopg → None(memory 未启用)。
    单例懒建;路由层据 None 返 503。
    """
    global _memory_store_built, _memory_store
    if not _memory_store_built:
        _memory_store_built = True
        cfg = acfg.agent_cfg()
        dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if dsn:
            read_dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
            pool_max = acfg.get(cfg, "memory.pool_max_size", 10)
            try:
                from codev_platform.agent.memory_store_pg import SqlMemoryStore
                _memory_store = SqlMemoryStore(dsn, read_dsn=read_dsn, max_size=pool_max)
            except Exception as e:  # noqa: BLE001 — 缺 psycopg / DSN 坏 → memory 不可用,不挂服务
                import sys
                print(f"[agent.deps] memory store 不可用({type(e).__name__}: {e})", file=sys.stderr)
                _memory_store = None
        # B1: recall_backend=vector 时, 包一层写时 embed 装饰器(全写路径自动入向量索引)。
        # 索引不可用(缺 chromadb/模型)→ 不包, 写库照常, 召回退回关键词。
        if _memory_store is not None:
            idx = get_memory_vector_index()
            if idx is not None:
                from codev_platform.agent.memory_store_vector import VectorSyncMemoryStore
                _memory_store = VectorSyncMemoryStore(_memory_store, idx)
    return _memory_store


_vec_index_built = False
_vec_index = None  # type: ignore[var-annotated]


def get_memory_vector_index():
    """agent memory 向量索引(B1)。仅 config memory.recall_backend=='vector' 时构建;缺依赖 → None。
    单例懒建。store-wrap 与 recall service 共用同一实例。"""
    global _vec_index_built, _vec_index
    if not _vec_index_built:
        _vec_index_built = True
        cfg = acfg.agent_cfg()
        if acfg.get(cfg, "memory.recall_backend", "local") == "vector":
            from codev_platform.agent.memory_vector_chroma import build_memory_vector_index
            _vec_index = build_memory_vector_index(cfg)
    return _vec_index


_rbac_store_built = False
_rbac_store = None  # type: ignore[var-annotated]


def get_rbac_store():
    """RBAC 作用域存储(memory M5)。未配 memory.pg_dsn / 缺 psycopg → None(回退 interim ACL)。
    单例懒建(同 get_memory_store 范式);调用方据 None 优雅回退,不 raise / 不自动建库。
    """
    global _rbac_store_built, _rbac_store
    if not _rbac_store_built:
        _rbac_store_built = True
        cfg = acfg.agent_cfg()
        dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN", cfg, "memory.pg_dsn")
        if dsn:
            read_dsn = acfg.env_or_config("CODEV_PLATFORM_MEMORY_DSN_READ", cfg, "memory.pg_dsn_read")
            pool_max = acfg.get(cfg, "memory.pool_max_size", 10)
            try:
                from codev_platform.agent.rbac_store_pg import RbacStore
                _rbac_store = RbacStore(dsn, read_dsn=read_dsn, max_size=pool_max)
            except Exception as e:  # noqa: BLE001 — 缺 psycopg / DSN 坏 → 回退 interim ACL,不挂服务
                import sys
                print(f"[agent.deps] rbac store 不可用({type(e).__name__}: {e})", file=sys.stderr)
                _rbac_store = None
    return _rbac_store


_recall_built = False
_recall_service = None  # type: ignore[var-annotated]


def get_recall_service():
    """记忆召回服务(M3)。无 memory store → None(召回不启用)。
    backend="local" → LocalRecallService 直查 PG;"vector" 接缝(未接,见 recall_service.py)。
    单例懒建。
    """
    global _recall_built, _recall_service
    if not _recall_built:
        _recall_built = True
        store = get_memory_store()
        if store is not None:
            cfg = acfg.agent_cfg()
            backend = acfg.get(cfg, "memory.recall_backend", "local")
            policy = acfg.get(cfg, "memory.conflict_policy", "personal_first")
            if backend == "vector":
                # B1: 向量语义召回。索引可用 → VectorRecallService(排序层 RRF 关键词∪向量);
                # 缺 chromadb/模型 → 退回 LocalRecallService(召回不挂, 只是退化为关键词)。
                idx = get_memory_vector_index()
                from codev_platform.agent.recall_service import (
                    LocalRecallService, VectorRecallService,
                )
                if idx is not None:
                    _recall_service = VectorRecallService(
                        store, idx, default_policy=policy, rbac_store=get_rbac_store())
                else:
                    import sys
                    print("[agent.deps] recall_backend=vector 但向量索引不可用(缺 chromadb/模型),"
                          "退回 local 关键词召回", file=sys.stderr)
                    _recall_service = LocalRecallService(
                        store, default_policy=policy, rbac_store=get_rbac_store())
            else:
                from codev_platform.agent.recall_service import LocalRecallService
                if backend != "local":
                    import sys
                    print(f"[agent.deps] memory.recall_backend={backend!r} 未知(仅 local/vector),"
                          f"按 local 处理", file=sys.stderr)
                _recall_service = LocalRecallService(
                    store, default_policy=policy, rbac_store=get_rbac_store())
    return _recall_service


def get_memory_maintenance():
    """记忆维护(M4:TTL 归档 + 压缩融合)。无 memory store → None。
    每次新建(job 调用频率低,无需缓存;底层复用 store 的连接池)。
    """
    store = get_memory_store()
    if store is None:
        return None
    cfg = acfg.agent_cfg()
    min_n = acfg.get(cfg, "memory.compress_min_entries", 3)
    from codev_platform.agent.memory_maintenance import MemoryMaintenance
    return MemoryMaintenance(store, min_entries=min_n)


_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    """ChatService 单例,注入进程级 sessions/registry + provider 工厂 + max_steps 解析 + 召回。"""
    global _chat_service
    if _chat_service is None:
        cfg = acfg.agent_cfg()
        _chat_service = ChatService(
            sessions=_sessions,
            registry_factory=build_default_registry,  # (project_id) -> registry,P2 多租户
            provider_factory=_get_provider,
            default_max_steps=lambda: acfg.max_steps(acfg.agent_cfg()),
            recall=get_recall_service(),
            recall_limit=acfg.get(cfg, "memory.recall_limit", 8),
            # 每模型循环策略:按 provider 名解析 spec 内置档 ⊕ config 覆盖(运行中切 provider 也即时生效)。
            loop_policy_factory=lambda name: _loop_policy(acfg.agent_cfg(), name),
        )
    return _chat_service
