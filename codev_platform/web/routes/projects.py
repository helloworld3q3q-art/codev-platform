"""Projects 路由 (plan §十五 Projects) —— list/register/detail/load/unload。

routes 只声明 method/path/operation_id + 调 service + 返回 envelope (plan §三):
不直接访问 repository, 不拼异常响应 (PlatformError 走统一异常处理器)。
operation_id 唯一英文 (plan §十三 OpenAPI 约束)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.web.schemas.projects import (
    ProjectActionResult,
    ProjectListItem,
    ProjectListRequest,
    ProjectLoadRequest,
    ProjectRegisterRequest,
)
from codev_platform.web.security.deps import current_session, require_org_role
from codev_platform.web.security.sessions import Session
from codev_platform.web.services.project_service import ProjectService

router = APIRouter()

_TAG = "ProjectAPI-项目管理"

# register/load/unload 的管理面授权: list/detail 仅需登录态 (current_session) + service 内逐项目闸;
# register 是组织级动作 (在本 org 建新项目) → 需 org admin。load/unload 的逐项目 write 闸在 service。
_require_admin = require_org_role("admin")


def _service() -> ProjectService:
    return ProjectService()


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


@router.post(
    "/api/v1/projects/list",
    tags=[_TAG],
    summary="项目管理-项目列表",
    operation_id="listProjects",
    response_model=PageResult[ProjectListItem],
)
def list_projects(
    request: Request,
    body: ProjectListRequest | None = None,
    svc: ProjectService = Depends(_service),
    sess: Session = Depends(current_session),
) -> PageResult[ProjectListItem]:
    # 参数走 POST body (前端 ResizableTable 发 body, 非 query); 空 body 用默认。
    # 可见性按登录会话: org 隔离 + 逐项目 (service.list_projects 内判); body.orgId 在可见集内再收窄。
    b = body or ProjectListRequest()
    offset = (b.pageNumber - 1) * b.pageSize
    items, total = svc.list_projects(
        sess=sess, filter_org_id=b.orgId, keyword=b.keyword,
        offset=offset, limit=b.pageSize,
    )
    return page(items, page_number=b.pageNumber, page_size=b.pageSize,
                total=total, request_id=_rid(request))


@router.post(
    "/api/v1/projects/register",
    tags=[_TAG],
    summary="项目管理-注册项目",
    operation_id="registerProject",
    response_model=CommonResult[ProjectActionResult],
)
def register_project(
    request: Request,
    body: ProjectRegisterRequest,
    svc: ProjectService = Depends(_service),
    sess: Session = Depends(_require_admin),
) -> CommonResult[ProjectActionResult]:
    # 组织级动作: 须 org admin (_require_admin)。新项目归属当前会话 org (防非超管跨 org 建项目);
    # 仅 platform_admin 可用 body.orgId 指定其它目标组织。
    from codev_platform.core.config import load_config
    from codev_platform.core.platform_admin import is_platform_admin
    if body.orgId and is_platform_admin(load_config(), sess.username):
        org_id = body.orgId
    else:
        org_id = sess.org_id
    result = svc.register_project(code=body.code, name=body.name,
                                  repo_path=body.repoPath, description=body.description,
                                  org_id=org_id)
    return ok(result, request_id=_rid(request))


@router.get(
    "/api/v1/projects/detail",
    tags=[_TAG],
    summary="项目管理-项目详情",
    operation_id="getProjectDetail",
    response_model=CommonResult[ProjectListItem],
)
def get_project_detail(
    request: Request,
    code: str = Query(..., min_length=1, max_length=64, description="项目编码"),
    svc: ProjectService = Depends(_service),
    sess: Session = Depends(current_session),
) -> CommonResult[ProjectListItem]:
    return ok(svc.get_detail(code, sess), request_id=_rid(request))


@router.post(
    "/api/v1/projects/load",
    tags=[_TAG],
    summary="项目管理-加载项目",
    operation_id="loadProject",
    response_model=CommonResult[ProjectActionResult],
)
def load_project(
    request: Request,
    body: ProjectLoadRequest,
    svc: ProjectService = Depends(_service),
    sess: Session = Depends(current_session),
) -> CommonResult[ProjectActionResult]:
    return ok(svc.load_project(body.code, sess), request_id=_rid(request))


@router.post(
    "/api/v1/projects/unload",
    tags=[_TAG],
    summary="项目管理-卸载项目",
    operation_id="unloadProject",
    response_model=CommonResult[ProjectActionResult],
)
def unload_project(
    request: Request,
    body: ProjectLoadRequest,
    svc: ProjectService = Depends(_service),
    sess: Session = Depends(current_session),
) -> CommonResult[ProjectActionResult]:
    return ok(svc.unload_project(body.code, sess), request_id=_rid(request))
