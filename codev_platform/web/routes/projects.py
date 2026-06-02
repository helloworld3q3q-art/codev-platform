"""Projects 路由 (plan §十五 Projects) —— list/register/detail/load/unload。

routes 只声明 method/path/operation_id + 调 service + 返回 envelope (plan §三):
不直接访问 repository, 不拼异常响应 (PlatformError 走统一异常处理器)。
operation_id 唯一英文 (plan §十三 OpenAPI 约束)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from codev_platform.core.httpkit.envelope import CommonResult, PageResult, ok, page
from codev_platform.core.httpkit.pagination import PageParams, page_params
from codev_platform.web.schemas.projects import (
    ProjectActionResult,
    ProjectListItem,
    ProjectLoadRequest,
    ProjectRegisterRequest,
)
from codev_platform.web.services.project_service import ProjectService

router = APIRouter()

_TAG = "ProjectAPI-项目管理"


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
    pg: PageParams = Depends(page_params),
    svc: ProjectService = Depends(_service),
) -> PageResult[ProjectListItem]:
    items, total = svc.list_projects(offset=pg.offset, limit=pg.page_size)
    return page(items, page_number=pg.page_number, page_size=pg.page_size,
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
) -> CommonResult[ProjectActionResult]:
    result = svc.register_project(code=body.code, name=body.name,
                                  repo_path=body.repoPath, description=body.description)
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
) -> CommonResult[ProjectListItem]:
    return ok(svc.get_detail(code), request_id=_rid(request))


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
) -> CommonResult[ProjectActionResult]:
    return ok(svc.load_project(body.code), request_id=_rid(request))


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
) -> CommonResult[ProjectActionResult]:
    return ok(svc.unload_project(body.code), request_id=_rid(request))
