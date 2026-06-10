"""登录态 + 授权依赖 (plan §五 / §十五) —— 供 Auth/Orgs/Users 路由复用。

- current_session: 解析 Bearer access token → Session, 失败 403 (与 gateway 静态 token 解耦)。
- require_org_role(action): org 级授权, 复用 core.rbac.role_allows; platform_admin bypass (§5.2 方案C)。
- require_platform_admin: 仅平台超管 (创建组织等)。

授权数据来自 web member_store (web 自有成员表), 故这些 dep 放 web 层而非 core.httpkit (叶子不依赖 web)。
"""
from __future__ import annotations

from fastapi import Request

from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.core.rbac import role_allows
from codev_platform.web.repositories.account_store import get_member_store
from codev_platform.web.security.sessions import Session, get_session_store


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("Authorization") or request.headers.get("authorization")
    if raw and raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return None


def current_session(request: Request) -> Session:
    """解析登录态; 无 / 失效 → 403。"""
    sess = get_session_store().resolve(_bearer(request) or "")
    if sess is None:
        raise PlatformError(ErrorCode.ACCESS_DENIED, "未登录或会话已失效")
    return sess


def require_platform_admin(request: Request) -> Session:
    sess = current_session(request)
    if not is_platform_admin(load_config(), sess.username):
        raise PlatformError(ErrorCode.ACCESS_DENIED, "需要平台管理员权限")
    return sess


def require_org_role(action: str = "admin"):
    """org 级授权依赖工厂 (action ∈ read|recall|write|admin)。platform_admin 直接放行。"""

    def _dep(request: Request) -> Session:
        sess = current_session(request)
        cfg = load_config()
        if is_platform_admin(cfg, sess.username):
            return sess
        member = get_member_store().get(sess.org_id, sess.username)
        role = member.role if member else None
        if not role_allows(role, action):
            raise PlatformError(ErrorCode.ACCESS_DENIED, f"需要组织 {action} 权限")
        return sess

    return _dep
