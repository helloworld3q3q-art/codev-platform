"""Memory 路由请求 / 响应模型 (B1 web 前门 → codev-agent 代理)。

camelCase 对齐其它 web schema (users/jobs); 路由层把这里的 camelCase 翻成 agent 侧
snake_case body (scope_ref / topic_key)。org_id 不在此暴露 —— 取 web 已认证身份, 经
X-Identity 签发给 agent, 杜绝 client 伪造租户 (红线)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class MemoryWriteRequest(BaseModel):
    """POST /api/v1/memory 写记忆 (代理 agent /memory)。"""

    scope: str = Field(..., min_length=1, description="org | team | project | personal")
    # personal 时可空 (路由强制用本人 user_id); 非 personal 由路由校验非空。
    scopeRef: str = Field("", description="org='org' / team_id / project_id / user_id; personal 留空")
    content: str = Field(..., min_length=1, description="记忆内容")
    kind: str | None = Field(None, description="preference | fact | task ...")
    topicKey: str | None = Field(None, description="冲突检测键")
    ttl: int | None = Field(None, ge=1, description="存活秒数 (省略=永久)")


class MemoryItem(BaseModel):
    """记忆条目对外视图 (agent /memory 返回的投影)。"""

    id: str = Field(..., description="记忆 ID")
    scope: str = Field(..., description="作用域")
    scopeRef: str = Field(..., description="作用域 ref")
    ownerUserId: str = Field(..., description="写入者 user_id")
    content: str = Field(..., description="记忆内容")
    orgId: str = Field(..., description="所属租户")
    kind: str | None = Field(None, description="类型")
    topicKey: str | None = Field(None, description="冲突检测键")
    isRedline: bool = Field(False, description="org 硬约束")
    status: str = Field("active", description="状态")

    @classmethod
    def of(cls, e: dict) -> MemoryItem:
        """agent /memory 返回的 snake_case dict → camelCase 视图。"""
        return cls(
            id=str(e.get("id", "")),
            scope=str(e.get("scope", "")),
            scopeRef=str(e.get("scope_ref", "")),
            ownerUserId=str(e.get("owner_user_id", "")),
            content=str(e.get("content", "")),
            orgId=str(e.get("org_id", "")),
            kind=e.get("kind"),
            topicKey=e.get("topic_key"),
            isRedline=bool(e.get("is_redline", False)),
            status=str(e.get("status", "active")),
        )
