"""Auth 路由 (plan §十五 Auth) —— login / logout / token-refresh / session。

routes 只声明 method/path/operation_id + 调 service + 返回 envelope (plan §三):
不直接访问 store, 不拼异常响应 (PlatformError 走统一异常处理器)。
operation_id 唯一英文 (plan §十三 OpenAPI 约束)。
login/logout/refresh 是公开端点 (不要求登录态); session 经 current_session 解析登录态。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.web.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    PublicKeyInfo,
    RefreshRequest,
    SessionInfo,
    TokenPair,
)
from codev_platform.web.security.deps import current_session
from codev_platform.web.security.membership import resolve_session_roles
from codev_platform.web.security.rsa_keys import get_keypair
from codev_platform.web.security.sessions import Session
from codev_platform.web.services.auth_service import AuthService

router = APIRouter()

_TAG = "AuthAPI-认证"


def _service() -> AuthService:
    return AuthService()


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


@router.get(
    "/api/v1/auth/public-key",
    tags=[_TAG],
    summary="认证-登录口令加密公钥",
    operation_id="authPublicKey",
    response_model=CommonResult[PublicKeyInfo],
)
def public_key(request: Request) -> CommonResult[PublicKeyInfo]:
    """前端登录前拉公钥, JSEncrypt 加密口令 (方案 B)。RSA 不可用时 publicKey 空 → 前端降级明文。"""
    kp = get_keypair()
    return ok(PublicKeyInfo(publicKey=kp.public_pem if kp else ""), request_id=_rid(request))


@router.post(
    "/api/v1/auth/login",
    tags=[_TAG],
    summary="认证-登录",
    operation_id="authLogin",
    response_model=CommonResult[TokenPair],
)
def login(
    request: Request,
    body: LoginRequest,
    svc: AuthService = Depends(_service),
) -> CommonResult[TokenPair]:
    pair = svc.login(username=body.username, password=body.password)
    return ok(pair, request_id=_rid(request))


@router.post(
    "/api/v1/auth/logout",
    tags=[_TAG],
    summary="认证-登出",
    operation_id="authLogout",
    response_model=CommonResult[None],
)
def logout(
    request: Request,
    body: LogoutRequest,
    svc: AuthService = Depends(_service),
) -> CommonResult[None]:
    svc.logout(refresh_token=body.refreshToken)
    return ok(request_id=_rid(request))


@router.post(
    "/api/v1/auth/token/refresh",
    tags=[_TAG],
    summary="认证-刷新令牌",
    operation_id="authRefreshToken",
    response_model=CommonResult[TokenPair],
)
def refresh_token(
    request: Request,
    body: RefreshRequest,
    svc: AuthService = Depends(_service),
) -> CommonResult[TokenPair]:
    pair = svc.refresh(refresh_token=body.refreshToken)
    return ok(pair, request_id=_rid(request))


@router.get(
    "/api/v1/auth/session",
    tags=[_TAG],
    summary="认证-当前会话",
    operation_id="authCurrentSession",
    response_model=CommonResult[SessionInfo],
)
def current(
    request: Request,
    sess: Session = Depends(current_session),
) -> CommonResult[SessionInfo]:
    roles = resolve_session_roles(sess)
    return ok(
        SessionInfo(username=sess.username, orgId=sess.org_id, roles=roles),
        request_id=_rid(request),
    )
