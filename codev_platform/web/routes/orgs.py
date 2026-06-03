"""Orgs 路由 (plan §十五 Orgs) —— 组织 CRUD/status/selections + 成员 list/add/remove/roles。

routes 只声明 method/path/operation_id + 授权 Depends + 调 service + 返回 envelope (plan §三):
不直接访问 store, 不拼异常响应 (PlatformError 走统一异常处理器)。
operation_id 唯一英文 (plan §十三 OpenAPI 约束)。
授权: 创建组织需平台超管 (require_platform_admin); 改组织 / 成员管理需 org_admin
(require_org_role("admin"), platform_admin bypass); 读类需登录态 (current_session)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.core.httpkit.pagination import PageParams, page_params
from codev_platform.web.schemas.orgs import (
    MemberActionResult,
    MemberAddRequest,
    MemberItem,
    MemberRemoveRequest,
    MemberRoleRequest,
    OrgActionResult,
    OrgCreateRequest,
    OrgItem,
    OrgSelectionItem,
    OrgStatusRequest,
    OrgUpdateRequest,
)
from codev_platform.web.security.deps import (
    current_session,
    require_org_role,
    require_platform_admin,
)
from codev_platform.web.services.org_service import OrgService

router = APIRouter()

_TAG = "OrgAPI-组织管理"

# org admin 授权依赖单例 (模块级, 避免在参数默认值里现调 require_org_role —— 同 projects.py 范式)。
_require_admin = require_org_role("admin")


def _service() -> OrgService:
    return OrgService()


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


# ---- 组织 ----

@router.post(
    "/api/v1/orgs/list",
    tags=[_TAG],
    summary="组织管理-组织列表",
    operation_id="listOrgs",
    response_model=PageResult[OrgItem],
)
def list_orgs(
    request: Request,
    pg: PageParams = Depends(page_params),
    _sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> PageResult[OrgItem]:
    items, total = svc.list_orgs(offset=pg.offset, limit=pg.page_size)
    return page(items, page_number=pg.page_number, page_size=pg.page_size,
                total=total, request_id=_rid(request))


@router.post(
    "/api/v1/orgs/create",
    tags=[_TAG],
    summary="组织管理-创建组织",
    operation_id="createOrg",
    response_model=CommonResult[OrgActionResult],
)
def create_org(
    request: Request,
    body: OrgCreateRequest,
    _sess=Depends(require_platform_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[OrgActionResult]:
    result = svc.create_org(code=body.code, name=body.name, description=body.description)
    return ok(result, request_id=_rid(request))


@router.get(
    "/api/v1/orgs/detail",
    tags=[_TAG],
    summary="组织管理-组织详情",
    operation_id="getOrgDetail",
    response_model=CommonResult[OrgItem],
)
def get_org_detail(
    request: Request,
    code: str = Query(..., min_length=1, max_length=64, description="组织编码"),
    _sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> CommonResult[OrgItem]:
    return ok(svc.get_detail(code), request_id=_rid(request))


@router.post(
    "/api/v1/orgs/update",
    tags=[_TAG],
    summary="组织管理-更新组织",
    operation_id="updateOrg",
    response_model=CommonResult[OrgActionResult],
)
def update_org(
    request: Request,
    body: OrgUpdateRequest,
    _sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[OrgActionResult]:
    result = svc.update_org(code=body.code, name=body.name, description=body.description)
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/orgs/status",
    tags=[_TAG],
    summary="组织管理-启用禁用",
    operation_id="setOrgStatus",
    response_model=CommonResult[OrgActionResult],
)
def set_org_status(
    request: Request,
    body: OrgStatusRequest,
    _sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[OrgActionResult]:
    return ok(svc.set_status(code=body.code, status=body.status), request_id=_rid(request))


@router.post(
    "/api/v1/orgs/selections",
    tags=[_TAG],
    summary="组织管理-组织下拉",
    operation_id="listOrgSelections",
    response_model=CommonResult[list[OrgSelectionItem]],
)
def list_org_selections(
    request: Request,
    _sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> CommonResult[list[OrgSelectionItem]]:
    return ok(svc.selections(), request_id=_rid(request))


# ---- 成员 ----

@router.post(
    "/api/v1/orgs/members/list",
    tags=[_TAG],
    summary="组织管理-成员列表",
    operation_id="listOrgMembers",
    response_model=PageResult[MemberItem],
)
def list_org_members(
    request: Request,
    code: str = Query(..., min_length=1, max_length=64, description="组织编码"),
    pg: PageParams = Depends(page_params),
    _sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> PageResult[MemberItem]:
    items, total = svc.list_members(code, offset=pg.offset, limit=pg.page_size)
    return page(items, page_number=pg.page_number, page_size=pg.page_size,
                total=total, request_id=_rid(request))


@router.post(
    "/api/v1/orgs/members/add",
    tags=[_TAG],
    summary="组织管理-添加成员",
    operation_id="addOrgMember",
    response_model=CommonResult[MemberActionResult],
)
def add_org_member(
    request: Request,
    body: MemberAddRequest,
    _sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    result = svc.add_member(code=body.code, username=body.username, role=body.role)
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/orgs/members/remove",
    tags=[_TAG],
    summary="组织管理-移除成员",
    operation_id="removeOrgMember",
    response_model=CommonResult[MemberActionResult],
)
def remove_org_member(
    request: Request,
    body: MemberRemoveRequest,
    _sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    result = svc.remove_member(code=body.code, username=body.username)
    return ok(result, request_id=_rid(request))


@router.post(
    "/api/v1/orgs/members/roles",
    tags=[_TAG],
    summary="组织管理-成员角色",
    operation_id="setOrgMemberRole",
    response_model=CommonResult[MemberActionResult],
)
def set_org_member_role(
    request: Request,
    body: MemberRoleRequest,
    _sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    result = svc.set_member_role(code=body.code, username=body.username, role=body.role)
    return ok(result, request_id=_rid(request))
