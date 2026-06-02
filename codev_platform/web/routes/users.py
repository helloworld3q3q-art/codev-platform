"""Users 路由 (plan §十五 Users) —— profile/list/create/detail/update/status/password/roles/selections。

routes 只声明 method/path/operation_id + 取登录态/授权 + 调 service + 返回 envelope (plan §三):
不直接访问 store, 不拼异常响应 (PlatformError 走统一异常处理器)。operation_id 唯一英文 (§十三)。

授权:
- profile: current_session (任意登录用户看自己)。
- 其余写/读他人: require_org_role("admin") (org 级 admin; platform_admin bypass 在 dep 内)。
越权细化 (org_admin 只管本 org) 在 service 层按 caller_org_id / caller_is_admin 判 (plan §十五)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.core.httpkit.pagination import PageParams, page_params
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.web.schemas.users import (
    UserActionResult,
    UserCreateRequest,
    UserItem,
    UserPasswordResetRequest,
    UserRolesRequest,
    UserSelectionItem,
    UserStatusRequest,
    UserUpdateRequest,
)
from codev_platform.web.security.deps import current_session, require_org_role
from codev_platform.web.security.sessions import Session
from codev_platform.web.services.user_service import UserService

router = APIRouter()

_TAG = "UserAPI-用户管理"
_admin = require_org_role("admin")


def _service() -> UserService:
    return UserService()


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


def _is_platform_admin(username: str) -> bool:
    return is_platform_admin(load_config(), username)


@router.get(
    "/api/v1/users/profile",
    tags=[_TAG],
    summary="用户管理-当前用户",
    operation_id="getUserProfile",
    response_model=CommonResult[UserItem],
)
def get_user_profile(
    request: Request,
    sess: Session = Depends(current_session),
    svc: UserService = Depends(_service),
) -> CommonResult[UserItem]:
    return ok(svc.profile(sess.username), request_id=_rid(request))


@router.post(
    "/api/v1/users/list",
    tags=[_TAG],
    summary="用户管理-用户列表",
    operation_id="listUsers",
    response_model=PageResult[UserItem],
)
def list_users(
    request: Request,
    pg: PageParams = Depends(page_params),
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> PageResult[UserItem]:
    org_id = None if _is_platform_admin(sess.username) else sess.org_id
    items, total = svc.list_users(org_id=org_id, offset=pg.offset, limit=pg.page_size)
    return page(items, page_number=pg.page_number, page_size=pg.page_size,
                total=total, request_id=_rid(request))


@router.post(
    "/api/v1/users/create",
    tags=[_TAG],
    summary="用户管理-创建用户",
    operation_id="createUser",
    response_model=CommonResult[UserActionResult],
)
def create_user(
    request: Request,
    body: UserCreateRequest,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserActionResult]:
    result = svc.create_user(
        username=body.username, password=body.password, org_id=body.orgId,
        display_name=body.displayName, email=body.email, role=body.role,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
    )
    return ok(result, request_id=_rid(request))


@router.get(
    "/api/v1/users/detail",
    tags=[_TAG],
    summary="用户管理-用户详情",
    operation_id="getUserDetail",
    response_model=CommonResult[UserItem],
)
def get_user_detail(
    request: Request,
    username: str = Query(..., min_length=1, max_length=64, description="用户名"),
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserItem]:
    item = svc.get_detail(username=username, caller_org_id=sess.org_id,
                          caller_is_admin=_is_platform_admin(sess.username))
    return ok(item, request_id=_rid(request))


@router.post(
    "/api/v1/users/update",
    tags=[_TAG],
    summary="用户管理-更新用户",
    operation_id="updateUser",
    response_model=CommonResult[UserActionResult],
)
def update_user(
    request: Request,
    body: UserUpdateRequest,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserActionResult]:
    result = svc.update_user(
        username=body.username, display_name=body.displayName, email=body.email,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
    )
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/users/status",
    tags=[_TAG],
    summary="用户管理-启用禁用",
    operation_id="setUserStatus",
    response_model=CommonResult[UserActionResult],
)
def set_user_status(
    request: Request,
    body: UserStatusRequest,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserActionResult]:
    result = svc.set_status(
        username=body.username, status=body.status,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
        actor=sess.username,
    )
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/users/password/reset",
    tags=[_TAG],
    summary="用户管理-重置密码",
    operation_id="resetUserPassword",
    response_model=CommonResult[UserActionResult],
)
def reset_user_password(
    request: Request,
    body: UserPasswordResetRequest,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserActionResult]:
    result = svc.reset_password(
        username=body.username, new_password=body.newPassword,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
        actor=sess.username,
    )
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/users/roles",
    tags=[_TAG],
    summary="用户管理-角色授权",
    operation_id="setUserRoles",
    response_model=CommonResult[UserActionResult],
)
def set_user_roles(
    request: Request,
    body: UserRolesRequest,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[UserActionResult]:
    result = svc.set_roles(
        username=body.username, org_id=body.orgId, role=body.role,
        caller_org_id=sess.org_id, caller_is_admin=_is_platform_admin(sess.username),
        actor=sess.username,
    )
    return ok(result, request_id=_rid(request))


@router.get(
    "/api/v1/users/selections",
    tags=[_TAG],
    summary="用户管理-用户选择器",
    operation_id="listUserSelections",
    response_model=CommonResult[list[UserSelectionItem]],
)
def list_user_selections(
    request: Request,
    sess: Session = Depends(_admin),
    svc: UserService = Depends(_service),
) -> CommonResult[list[UserSelectionItem]]:
    org_id = None if _is_platform_admin(sess.username) else sess.org_id
    return ok(svc.selections(org_id=org_id), request_id=_rid(request))
