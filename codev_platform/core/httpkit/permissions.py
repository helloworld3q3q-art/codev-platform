"""权限依赖工厂 —— FastAPI 依赖, 内部调 core.acl.can_access (plan §五 / D6)。

单一鉴权真值源: web 路由经此依赖复用 core.acl, 与 3 MCP + agent + memory 同一套模型
(让 can_access 成第 6 个 consumer, 不重造)。identity 来自 gateway.AuthMiddleware 写的
request.state.identity; project_id 来自 X-Project-Id header。deny → 抛 PlatformError(ACCESS_DENIED)
由统一异常处理器转 envelope; 同时写审计 (deny 永远记)。

platform_admin (plan §5.2 方案 C): identity.is_platform_admin → bypass (此处与 can_access 顶部两道闸,
都认同一标志); 真值源两阶段 (config 白名单 → PG 表), 由认证层注入到 identity, 本模块只读。
"""
from __future__ import annotations

from fastapi import Request

from codev_platform.core import acl, audit
from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError

_SERVICE = "web"


def _identity(request: Request):
    return getattr(request.state, "identity", None)


def require_project_access(request: Request):
    """项目访问闸: 校验 request.state.identity 能否访问 X-Project-Id。

    返回 (identity, project_id) 供路由继续用。deny → PlatformError(ACCESS_DENIED) + 审计。
    platform_admin bypass 在 can_access 顶部已含 (此处直接走 can_access 即可, 标志由 identity 带)。
    """
    identity = _identity(request)
    project_id = request.headers.get("X-Project-Id") or request.headers.get("x-project-id")
    cfg = load_config()
    decision = acl.can_access(cfg, identity, project_id)
    audit.audit_access(_SERVICE, identity, project_id, decision)
    if not decision.allowed:
        raise PlatformError(ErrorCode.ACCESS_DENIED, "Current user cannot access this project.",
                            detail=decision.reason)
    return identity, project_id
