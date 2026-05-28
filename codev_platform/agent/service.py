"""FastAPI 应用工厂. 入口: codev-platform agent serve.

只负责建 app + 挂 router;路由实现在 routes/,DI 在 deps.py,工具组装在 tools/。
端点:POST /chat | GET /health | GET /providers。
"""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="codev-platform agent", version="0.1.0")
    from codev_platform.agent.routes import chat, meta
    app.include_router(meta.router)
    app.include_router(chat.router)
    return app


# 模块级实例,供 uvicorn "codev_platform.agent.service:app" 引用
app = create_app()
