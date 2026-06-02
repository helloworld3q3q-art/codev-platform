"""Auth 模块 request/response 模型 (plan §十五 Auth / §七 字段规范)。

字段统一走 camelCase (accessToken 等), schema 层做第一层校验 (§八)。
密码明文只在 request 入参短暂存在, 绝不入域模型 / 不落库 / 不日志 (security.md)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """登录请求。username + 明文 password (校验后立即丢弃, 不落库)。"""

    username: str = Field(..., min_length=1, max_length=128, description="用户名")
    password: str = Field(..., min_length=1, max_length=256, description="密码 (明文, 仅校验用)")


class LogoutRequest(BaseModel):
    """登出请求。撤销 refreshToken 对应会话。"""

    refreshToken: str = Field(..., min_length=1, max_length=512, description="refresh token")


class RefreshRequest(BaseModel):
    """刷新请求。refreshToken 换新 token 对 (旧 refresh 轮换失效)。"""

    refreshToken: str = Field(..., min_length=1, max_length=512, description="refresh token")


class TokenPair(BaseModel):
    """登录 / 刷新返回的 token 对。"""

    accessToken: str
    refreshToken: str


class SessionInfo(BaseModel):
    """当前会话信息 (GET session 返回)。"""

    username: str
    orgId: str
