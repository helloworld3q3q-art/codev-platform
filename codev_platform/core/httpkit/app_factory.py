"""FastAPI app 工厂样板 —— agent 与 web 都调 (plan D7)。

封"任意 HTTP 入口共用"的装配: 挂 gateway 中间件栈 (RateLimit→Auth) + RequestId (最外层)
+ 统一异常处理器 (PlatformError / 校验错误 / 兜底 → envelope) + /health 探针。

中间件栈序 (add_middleware 后加在外层):
  先加 RateLimit (最内) → 再加 Auth → 最后加 RequestId (最外, 让 401/异常也带 request_id)。
保持与 gateway/middleware.py 既有纯 ASGI 中间件一致, 不重造。
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.httpkit.envelope import error_response, internal_error_response
from codev_platform.core.httpkit.request_id import RequestIdMiddleware


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def _platform_err(request: Request, exc: PlatformError):  # noqa: ANN202
        return error_response(exc, request_id=_request_id(request))

    @app.exception_handler(RequestValidationError)
    async def _validation_err(request: Request, exc: RequestValidationError):  # noqa: ANN202
        # 入参校验失败 → INVALID_PARAMS;detail 仅日志, 不回显原始报文 (防泄漏内部结构)。
        err = PlatformError(ErrorCode.INVALID_PARAMS, "invalid request params", detail=str(exc))
        return error_response(err, request_id=_request_id(request))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):  # noqa: ANN202
        # 兜底: 不泄漏 str(e), 只回 internal + request_id (详情由上游 logging 落)。
        return internal_error_response(request_id=_request_id(request))


def build_app(
    *,
    title: str,
    routers: Iterable,
    public_paths: Iterable[str] = ("/health",),
    version: str = "0.1.0",
    cfg: dict | None = None,
    authenticator=None,
) -> FastAPI:
    """建 app: 挂 routers + gateway 中间件 + RequestId + 统一异常处理。

    cfg 缺省走 load_config(); 测试可注入。public_paths 默认放行 /health。
    authenticator 缺省按 cfg 选 (build_authenticator); web app 传 SessionAwareAuthenticator
    包一层, 让 token 模式也认 web 登录 session token (双轨收口)。
    """
    from codev_platform.gateway import (
        AuthMiddleware,
        build_authenticator,
        maybe_rate_limit_middleware,
    )

    app = FastAPI(title=title, version=version)
    for r in routers:
        app.include_router(r)

    _install_exception_handlers(app)

    _cfg = cfg if cfg is not None else load_config()
    _rl = maybe_rate_limit_middleware(_cfg)
    if _rl is not None:
        app.add_middleware(_rl.cls, **_rl.kwargs)
    app.add_middleware(
        AuthMiddleware,
        authenticator=authenticator if authenticator is not None else build_authenticator(_cfg),
        public_paths=set(public_paths),
    )
    app.add_middleware(RequestIdMiddleware)  # 最外层: 标记所有响应 (含 401/异常)
    return app
