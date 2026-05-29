"""FastAPI 应用工厂. 入口: codev-platform agent serve.

只负责建 app + 挂 router + 网关中间件;路由实现在 routes/,DI 在 deps.py,工具在 tools/,
认证/统一拦截在 gateway/(解耦,不写进这里)。端点:POST /chat | GET /health | GET /providers。
"""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="codev-platform agent", version="0.1.0")
    from codev_platform.agent.routes import chat, meta, memory
    app.include_router(meta.router)
    app.include_router(chat.router)
    app.include_router(memory.router)

    # 统一请求拦截(认证 → request.state.identity)。passthrough(单人)放行解析,
    # token 模式(M6)对非公开端点 401。/health 公开(存活探针)。模块在 gateway/,此处只挂载。
    from codev_platform.core.config import load_config
    from codev_platform.gateway import AuthMiddleware, build_authenticator
    app.add_middleware(
        AuthMiddleware,
        authenticator=build_authenticator(load_config()),
        public_paths={"/health"},
    )
    return app


# 模块级实例,供 uvicorn "codev_platform.agent.service:app" 引用
app = create_app()
