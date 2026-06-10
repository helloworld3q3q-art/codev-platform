"""Orgs 路由 (plan §十五 Orgs) —— 组织 CRUD/status/selections + 成员 list/add/remove/roles。

routes 只声明 method/path/operation_id + 授权 Depends + 调 service + 返回 envelope (plan §三):
不直接访问 store, 不拼异常响应 (PlatformError 走统一异常处理器)。
operation_id 唯一英文 (plan §十三 OpenAPI 约束)。
授权: 创建组织需平台超管 (require_platform_admin); 改组织 / 成员管理需 org_admin
(require_org_role("admin"), platform_admin bypass); 读类需登录态 (current_session)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.core.httpkit.pagination import PageBody
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.web.schemas.orgs import (
    MemberActionResult,
    MemberAddRequest,
    MemberItem,
    MemberListRequest,
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


def _guard_target_org(sess, code: str) -> None:
    """org 成员管理越权护栏(跨 org 隔离红线): 非 platform_admin 只能操作**自己 (session) org** 的成员。

    require_org_role('admin') 只校验 caller 是其 session org 的 admin, 但成员端点的目标 org 是 body.code
    (任意 org)—— 不加这条, orgA admin 传 code=orgB 就能把任意人写成 orgB 成员/admin = 跨 org 提权;
    成员列表也会泄露别 org 成员名单。platform_admin 可跨 org(运维特权)。
    """
    if not is_platform_admin(load_config(), sess.username) and code != sess.org_id:
        raise PlatformError(ErrorCode.ACCESS_DENIED, "org_admin 不能管理其他组织的成员")


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
    body: PageBody | None = None,   # 分页从 body 取(前端 post 发 body); 缺/空 → 默认第 1 页
    _sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> PageResult[OrgItem]:
    pg = body or PageBody()
    items, total = svc.list_orgs(offset=pg.offset, limit=pg.pageSize)
    return page(items, page_number=pg.pageNumber, page_size=pg.pageSize,
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
    body: MemberListRequest,
    sess=Depends(current_session),
    svc: OrgService = Depends(_service),
) -> PageResult[MemberItem]:
    _guard_target_org(sess, body.code)  # 非超管不能列别 org 成员(防成员名单跨 org 泄露)
    # code/分页从 body 取(前端 post() 一律发 body, 不是 query); 修 members/list 400。
    offset = (body.pageNumber - 1) * body.pageSize
    items, total = svc.list_members(body.code, offset=offset, limit=body.pageSize)
    return page(items, page_number=body.pageNumber, page_size=body.pageSize,
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
    sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    _guard_target_org(sess, body.code)  # 🔴 防 orgA admin 用 code=orgB 把人写进 orgB(跨 org 提权)
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
    sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    _guard_target_org(sess, body.code)  # 防跨 org 移除别 org 成员
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
    sess=Depends(_require_admin),
    svc: OrgService = Depends(_service),
) -> CommonResult[MemberActionResult]:
    _guard_target_org(sess, body.code)  # 🔴 防 orgA admin 改 orgB 成员角色(跨 org 提权)
    result = svc.set_member_role(code=body.code, username=body.username, role=body.role)
    return ok(result, request_id=_rid(request))
