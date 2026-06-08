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


def create_app(cfg: dict | None = None) -> FastAPI:
    # 启动期: 绑定账户存储后端 (PG/内存) + 播种首个 platform_admin (装机 bootstrap, 幂等)。
    from codev_platform.core.config import load_config
    from codev_platform.core.httpkit.permissions import set_session_access_checker
    from codev_platform.web.repositories.account_store import bind_account_stores
    from codev_platform.web.security.bootstrap import ensure_seed_admin
    from codev_platform.web.services.project_service import session_project_decision

    _cfg = cfg if cfg is not None else load_config()
    bind_account_stores(_cfg)
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
            agent.router,
            memory.router,
            audit.router,
        ],
        public_paths=_PUBLIC_PATHS,
        cfg=_cfg,
        authenticator=SessionAwareAuthenticator(build_authenticator(_cfg)),
    )


app = create_app()
