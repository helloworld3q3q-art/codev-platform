"""Web Backend FastAPI app 工厂 (plan D1/D7) —— 独立进程, 调 httpkit.build_app。

uvicorn 引用: codev_platform.web.app:app。中间件/异常/RequestId 全在 httpkit.build_app,
本模块只列 routers + 公开 path。新增模块路由在此 include。
"""
from __future__ import annotations

from fastapi import FastAPI

from codev_platform.core.httpkit import build_app
from codev_platform.web.routes import (
    auth,
    enums,
    graph,
    health,
    indexes,
    jobs,
    orgs,
    projects,
    users,
)

# login / refresh 是登录入口, token 模式下须公开 (未持登录态即可访问); 其余走 gateway + 会话闸。
_PUBLIC_PATHS = {
    "/health",
    "/api/v1/health/check",
    "/api/v1/auth/login",
    "/api/v1/auth/token/refresh",
}


def create_app(cfg: dict | None = None) -> FastAPI:
    # 启动期: 绑定账户存储后端 (PG/内存) + 播种首个 platform_admin (装机 bootstrap, 幂等)。
    from codev_platform.core.config import load_config
    from codev_platform.web.repositories.account_store import bind_account_stores
    from codev_platform.web.security.bootstrap import ensure_seed_admin

    _cfg = cfg if cfg is not None else load_config()
    bind_account_stores(_cfg)
    ensure_seed_admin(_cfg)

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
        ],
        public_paths=_PUBLIC_PATHS,
        cfg=_cfg,
    )


app = create_app()
