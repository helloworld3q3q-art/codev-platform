"""Auth 模块 request/response 模型 (plan §十五 Auth / §七 字段规范)。

字段统一走 camelCase (accessToken 等), schema 层做第一层校验 (§八)。
密码明文只在 request 入参短暂存在, 绝不入域模型 / 不落库 / 不日志 (security.md)。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """登录请求。username + password(前端 RSA 加密的 base64, 或明文兼容; 校验后即丢弃)。"""

    username: str = Field(..., min_length=1, max_length=128, description="用户名")
    # max_length 放宽到 512 容纳 RSA/PKCS1v1.5 密文的 base64 (2048-bit → ~344 字符)。
    password: str = Field(..., min_length=1, max_length=512, description="密码 (RSA 加密 base64 或明文)")


class PublicKeyInfo(BaseModel):
    """登录口令加密用 RSA 公钥 (PEM, SubjectPublicKeyInfo)。前端 JSEncrypt setPublicKey 用。"""

    publicKey: str


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
    # 可信角色清单 (后端从 membership/platform_admin 算, 不信 client); 前端 isAdminRole 消费做菜单显隐。
    roles: list[str] = Field(default_factory=list, description="会话用户角色 (platform_admin/admin/member/viewer)")
