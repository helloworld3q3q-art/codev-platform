"""Audit 路由 (B1) —— 只读查询访问审计日志 (data_root/audit/access.jsonl)。

POST /api/v1/audit/list  过滤 + 分页查审计记录。

授权 require_org_role("admin"): 普通成员 403。org 边界硬隔离:
  - org_admin: 强制只查本 org (注入 session.org_id, 忽略 client 传的 orgId, 防越权看他组);
  - platform_admin: 可传 orgId 跨 org 查 (不传则查全部)。
routes 只声明 path/operation_id + 授权 + 调 repo + 返回 envelope (照 orgs.py/jobs.py 范式)。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from codev_platform.core.config import load_config
from codev_platform.core.httpkit.envelope import PageResult, page
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.web.repositories.audit_read_repo import AuditFilter, query
from codev_platform.web.schemas.audit import AuditItem, AuditListRequest
from codev_platform.web.security.deps import require_org_role
from codev_platform.web.security.sessions import Session

router = APIRouter()

_TAG = "AuditAPI-访问审计"
_require_admin = require_org_role("admin")  # 模块级单例 (同 orgs.py 范式)


def _rid(request: Request):
    return getattr(request.state, "request_id", None)


def _scoped_org_id(sess: Session, requested: str | None) -> str | None:
    """org 边界: platform_admin 采信 client orgId (跨 org); 否则强制本 org。"""
    if is_platform_admin(load_config(), sess.username):
        return requested
    return sess.org_id


@router.post(
    "/api/v1/audit/list",
    tags=[_TAG],
    summary="访问审计-审计日志列表",
    operation_id="listAuditAccess",
    response_model=PageResult[AuditItem],
)
def list_audit_access(
    request: Request,
    body: AuditListRequest,
    sess: Session = Depends(_require_admin),
) -> PageResult[AuditItem]:
    f = AuditFilter(
        service=body.service,
        user_id=body.userId,
        org_id=_scoped_org_id(sess, body.orgId),
        project_id=body.projectId,
        allowed=body.allowed,
        ts_from=body.tsFrom,
        ts_to=body.tsTo,
    )
    # 分页从 body 取(AuditListRequest 继承 PageBody); 前端 post 发 body, 不读 query。
    rows, total = query(f, offset=body.offset, limit=body.pageSize)
    return page(
        [AuditItem.of(r) for r in rows],
        page_number=body.pageNumber, page_size=body.pageSize, total=total,
        request_id=_rid(request),
    )
