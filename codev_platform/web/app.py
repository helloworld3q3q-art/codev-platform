"""Web Backend FastAPI app 工厂 (plan D1/D7) —— 独立进程, 调 httpkit.build_app。

uvicorn 引用: codev_platform.web.app:app。中间件/异常/RequestId 全在 httpkit.build_app,
本模块只列 routers + 公开 path。新增模块路由在此 include。
"""
from __future__ import annotations

from fastapi import FastAPI

from codev_platform.core.httpkit import build_app
from codev_platform.web.routes import (
    agent,
    audit,
    auth,
    enums,
    graph,
    health,
    indexes,
    jobs,
    memory,
    orgs,
    projects,
    recall,
    reports,
    users,
)

# login / refresh 是登录入口, token 模式下须公开 (未持登录态即可访问); 其余走 gateway + 会话闸。
_PUBLIC_PATHS = {
    "/health",
    "/api/v1/health/check",
    "/api/v1/auth/login",
    "/api/v1/auth/token/refresh",
    "/api/v1/auth/public-key",  # 登录前拉口令加密公钥 (方案 B)
}


def rebind_web_services(cfg: dict) -> None:
    """把 import 期绑了 `load_config()` 的 web 单例按显式 cfg 统一重绑(session / job / index / agent_client)。

    背景: 这些单例在各自模块 import 时就 `bind_*(load_config())` 绑死。`create_app(cfg)` 的显式 cfg
    原本只作用 app factory + authenticator → 测试注入 cfg / 多实例时 authenticator 用一份 cfg、而
    session/job 仍用 import 期的 load_config(),两份不一致。本函数把这几处收进一处统一按 cfg 重绑。

    **仅 create_app(cfg) 显式传 cfg 时调用**: 单实例生产 `create_app()` 不传 cfg → 不重绑 →
    import 期绑定即最终绑定, 行为完全不变(也避免在 import 期的 `app = create_app()` 凭空多造一份
    SessionStore)。重赋模块属性而非 from-import 捕获: 同模块 handler 调用期查全局即见新值; 跨模块
    session 消费方走 `get_session_store()` 见新值(复刻 account_store getter 范式)。
    """
    from codev_platform.web.integrations.agent_client import AgentClient
    from codev_platform.web.security import sessions
    from codev_platform.web.security.sessions import bind_session_store
    from codev_platform.web.services.index_service import (
        IndexService,
        make_reindex_dispatch_trigger,
    )
    from codev_platform.web.services.job_service import bind_job_service

    sessions.session_store = bind_session_store(cfg)
    jobs.job_service = bind_job_service(cfg, trigger=make_reindex_dispatch_trigger())
    indexes.index_service = IndexService(jobs.job_service)
    agent.agent_client = AgentClient(cfg)
    memory.agent_client = AgentClient(cfg)


def create_app(cfg: dict | None = None) -> FastAPI:
    # 启动期: 绑定账户存储后端 (PG/内存) + 播种首个 platform_admin (装机 bootstrap, 幂等)。
    from codev_platform.core.config import load_config
    from codev_platform.core.httpkit.permissions import set_session_access_checker
    from codev_platform.web.repositories.account_store import bind_account_stores
    from codev_platform.web.security.bootstrap import ensure_seed_admin
    from codev_platform.web.services.project_service import session_project_decision

    _cfg = cfg if cfg is not None else load_config()
    bind_account_stores(_cfg)
    # 显式 cfg(测试注入 / 多实例)→ 把 session/job/agent 单例也按同一 cfg 重绑, 消除 cfg 不一致;
    # 不传 cfg(单实例生产默认)→ 不重绑, import 期绑定不变。
    if cfg is not None:
        rebind_web_services(cfg)
    ensure_seed_admin(_cfg)
    # via=session 授权注入(仅 web 进程调 create_app): 让 require_project_access 的 web 登录态走
    # web RBAC。MCP/agent 进程不注入 → session 身份恒 deny(codex P2 #7, 物理隔离)。
    set_session_access_checker(session_project_decision)

    # 双轨收口: web 控制台用 session token 鉴权, 包一层 SessionAwareAuthenticator —— token 模式下
    # 既认 web 登录 session token, 又保留 gateway 静态 token 给 MCP/程序化访问。
    from codev_platform.gateway import build_authenticator
    from codev_platform.web.security.session_authenticator import SessionAwareAuthenticator

    return build_app(
        title="codev-platform web",
        routers=[
            health.router,
            auth.router,
            enums.router,
            orgs.router,
            users.router,
            projects.router,
            graph.router,
            jobs.router,
            indexes.router,
            reports.router,
            recall.router,
            agent.router,
            memory.router,
            audit.router,
        ],
        public_paths=_PUBLIC_PATHS,
        cfg=_cfg,
        authenticator=SessionAwareAuthenticator(build_authenticator(_cfg)),
    )


app = create_app()
