"""审计访问日志请求 + 响应模型 (camelCase, 对齐前端约定)。

AuditListRequest 是过滤条件; AuditItem 是 access.jsonl 单条记录的对外投影
(字段名映射 snake_case → camelCase)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AuditListRequest(BaseModel):
    """POST /api/v1/audit/list 请求体 —— 过滤条件 (全可选)。

    注: org 维度不在此声明 —— org_admin 强制只查本 org (路由注入 session org),
    platform_admin 可传 orgId 跨 org 查。
    """

    service: str | None = Field(None, description="服务名: codev-web / codev-agent")
    userId: str | None = Field(None, description="用户 ID")
    orgId: str | None = Field(None, description="组织 ID (仅 platform_admin 可指定; org_admin 忽略)")
    projectId: str | None = Field(None, description="项目 ID")
    allowed: bool | None = Field(None, description="授权结果: true 放行 / false 拒绝")
    tsFrom: str | None = Field(None, description="起始时间 ISO8601 (含界)")
    tsTo: str | None = Field(None, description="结束时间 ISO8601 (含界)")


class AuditItem(BaseModel):
    """审计记录对外视图 (access.jsonl 单条)。"""

    ts: str | None = Field(None, description="时间 ISO8601")
    service: str | None = Field(None, description="服务名")
    userId: str | None = Field(None, description="用户 ID")
    orgId: str | None = Field(None, description="组织 ID")
    via: str | None = Field(None, description="鉴权方式 (token / passthrough)")
    projectId: str | None = Field(None, description="项目 ID")
    allowed: bool = Field(False, description="是否放行")
    reason: str | None = Field(None, description="判定原因")

    @classmethod
    def of(cls, rec: dict) -> AuditItem:
        return cls(
            ts=rec.get("ts"),
            service=rec.get("service"),
            userId=rec.get("user_id"),
            orgId=rec.get("org_id"),
            via=rec.get("via"),
            projectId=rec.get("project_id"),
            allowed=bool(rec.get("allowed")),
            reason=rec.get("reason"),
        )
