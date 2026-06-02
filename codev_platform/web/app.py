"""Web Backend FastAPI app 工厂 (plan D1/D7) —— 独立进程, 调 httpkit.build_app。

uvicorn 引用: codev_platform.web.app:app。中间件/异常/RequestId 全在 httpkit.build_app,
本模块只列 routers + 公开 path。新增模块路由在此 include。
"""
from __future__ import annotations

from fastapi import FastAPI

from codev_platform.core.httpkit import build_app
from codev_platform.web.routes import enums, graph, health, indexes, jobs, projects

_PUBLIC_PATHS = {"/health", "/api/v1/health/check"}


def create_app(cfg: dict | None = None) -> FastAPI:
    return build_app(
        title="codev-platform web",
        routers=[
            health.router,
            enums.router,
            projects.router,
            graph.router,
            jobs.router,
            indexes.router,
        ],
        public_paths=_PUBLIC_PATHS,
        cfg=cfg,
    )


app = create_app()
