"""Tokens 路由 —— PG agent token 签发 / 列出 / 吊销 (web 控制台)。

routes 只声明 method/path/operation_id + 取登录态/授权 + 调 service + 返回 envelope:
不直接访问 store, 不拼异常响应 (PlatformError 走统一异常处理器)。operation_id 唯一英文。

授权: require_org_role("admin") (org 级 admin; platform_admin bypass 在 dep 内)。
**安全红线**: org_id 取认证 `session.org_id`, **绝不由 client body** —— token 越权签发第一道防线
(见 rbac-multi-org-membership-model)。越权细化 (本 org 成员 / 本 org token) 在 service 层判。
platform_admin 跨 org 精确签发走 CLI `gateway pg-token issue --org`; web MVP 签到签发者本 org。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import CommonResult, ok
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.web.schemas.tokens import (
    TokenActionResult,
    TokenIssueRequest,
    TokenIssueResult,
    TokenItem,
    TokenListRequest,
    TokenRevokeRequest,
)
from codev_platform.web.security.deps import require_org_role
from codev_platform.web.security.sessions import Session
from codev_platform.web.services.token_service import TokenService

router = APIRouter()

_TAG = "TokenAPI-接入令牌"
_admin = require_org_role("admin")


def _service() -> TokenService:
    return TokenService()


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


def _is_platform_admin(username: str) -> bool:
    return is_platform_admin(load_config(), username)


@router.post(
    "/api/v1/tokens/issue",
    tags=[_TAG],
    summary="接入令牌-签发",
    operation_id="issueToken",
    response_model=CommonResult[TokenIssueResult],
)
def issue_token_route(
    request: Request,
    body: TokenIssueRequest,
    sess: Session = Depends(_admin),
    svc: TokenService = Depends(_service),
) -> CommonResult[TokenIssueResult]:
    # org_id = sess.org_id (绝不由 client body, 红线): 签发者本 org。caller_is_admin=platform_admin bypass。
    result = svc.issue(
        target_user=body.targetUser, projects=body.projects, label=body.label,
        expires=body.expires, org_id=sess.org_id,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
    )
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/tokens/list",
    tags=[_TAG],
    summary="接入令牌-列表",
    operation_id="listTokens",
    response_model=CommonResult[list[TokenItem]],
)
def list_tokens_route(
    request: Request,
    body: TokenListRequest | None = None,
    sess: Session = Depends(_admin),
    svc: TokenService = Depends(_service),
) -> CommonResult[list[TokenItem]]:
    items = svc.list_tokens(
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
        target_user=(body.targetUser if body else None),
    )
    return ok(items, request_id=_rid(request))


@router.post(
    "/api/v1/tokens/revoke",
    tags=[_TAG],
    summary="接入令牌-吊销",
    operation_id="revokeToken",
    response_model=CommonResult[TokenActionResult],
)
def revoke_token_route(
    request: Request,
    body: TokenRevokeRequest,
    sess: Session = Depends(_admin),
    svc: TokenService = Depends(_service),
) -> CommonResult[TokenActionResult]:
    result = svc.revoke(
        token_hash_prefix=body.tokenHashPrefix,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
    )
    return ok(result, request_id=_rid(request))
