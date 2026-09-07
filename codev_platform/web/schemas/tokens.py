"""Tokens 模块 request/response 模型 —— IDE agent 接入 token 的 web 签发 / 列出 / 吊销。

PG token(`agent_tokens` 表)→user 走库, 认证时 join users.status 实时校验(web 禁用即失效);
明文只在 issue 一次返回, 库里只存 sha256 hash(见 gateway/token_store_pg)。字段统一 camelCase。

安全(rbac-multi-org-membership-model):org_id **不在 request** —— 由路由取认证 session.org_id,
绝不由 client body 传(防越权签发到任意 org)。projects 是项目访问白名单(ACL 闸2 真值)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class TokenIssueRequest(BaseModel):
    """签发 PG token(org admin)。org_id 不在此 —— 路由取 session.org_id(防越权, 见 docstring)。"""

    targetUser: str = Field(..., min_length=1, max_length=64, description="token 归属用户 (须已存在)")
    projects: str | None = Field(
        None, max_length=2000,
        description="项目访问白名单: '*' 全部 | 'pid1,pid2' 逗号分隔 | 缺省=无项目权(安全默认)",
    )
    label: str | None = Field(None, max_length=200, description="人读备注 (如 'alice laptop')")
    expires: str | None = Field(
        None, max_length=16, description="有效期 30d/12h/90m/45s; 缺省/空=永久",
    )


class TokenListRequest(BaseModel):
    """列 token。targetUser 给定则只列该用户; org_admin 只见本 org(service 按 org 过滤)。"""

    targetUser: str | None = Field(None, max_length=64, description="只列该用户的 token; 缺省=本 org 全部")


class TokenRevokeRequest(BaseModel):
    """吊销 token(按 hash 前缀)。service 校验命中 token 属本 org 方可吊销(防跨 org 越权)。"""

    tokenHashPrefix: str = Field(..., min_length=4, max_length=64, description="token hash 前缀 (token-list 可见)")


class TokenIssueResult(BaseModel):
    """签发回执 —— **明文 token 只此一次返回**, 不可再得(库里只存 hash, security.md)。"""

    token: str = Field(..., description="明文 token (只此一次; 用作 Bearer; 关闭即不可再得)")
    userId: str
    orgId: str
    projects: str | list[str] | None = None
    expiresAt: float | None = Field(None, description="过期 epoch 秒; None=永久")


class TokenItem(BaseModel):
    """token 列表项 —— **不含明文**(只有 hash 前缀, security.md)。"""

    userId: str
    orgId: str
    projects: str | list[str] | None = None
    label: str | None = None
    status: str = "ACTIVE"
    expiresAt: float | None = None
    tokenHashPrefix: str = Field("", description="token hash 前缀 (吊销用; 明文不可得)")


class TokenActionResult(BaseModel):
    """revoke 写操作回执(不含敏感字段)。"""

    revoked: int = Field(0, description="受影响 token 数")
