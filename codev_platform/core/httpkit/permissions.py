"""权限依赖工厂 —— FastAPI 依赖, 内部调 core.acl.can_access (plan §五 / D6)。

单一鉴权真值源: web 路由经此依赖复用 core.acl, 与 3 MCP + agent + memory 同一套模型
(让 can_access 成第 6 个 consumer, 不重造)。identity 来自 gateway.AuthMiddleware 写的
request.state.identity; project_id 来自 X-Project-Id header。deny → 抛 PlatformError(ACCESS_DENIED)
由统一异常处理器转 envelope; 同时写审计 (deny 永远记)。

platform_admin (plan §5.2 方案 C): identity.is_platform_admin → bypass (此处与 can_access 顶部两道闸,
都认同一标志); 真值源两阶段 (config 白名单 → PG 表), 由认证层注入到 identity, 本模块只读。
"""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from codev_platform.core import acl, audit
from codev_platform.core.config import load_config
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.platform_admin import is_platform_admin

_SERVICE = "web"

# via=session 的逐项目授权钩子: core/httpkit 不 import web, 由 web 启动注入
# project_service.session_project_decision。未注入(MCP/agent 进程) → session 身份恒 deny
# (session 只该在 web 出现, 物理隔离不靠约定)。codex P2 #7: prod token 模式下 web 登录能访问项目路由。
_session_checker: Callable[[object, str | None], acl.AccessDecision] | None = None


def set_session_access_checker(fn: Callable[[object, str | None], acl.AccessDecision]) -> None:
    """web 启动期注入 session 授权实现(只 web 进程调; MCP/agent 不调 → session 恒 deny)。"""
    global _session_checker
    _session_checker = fn


def _reset_session_access_checker() -> None:
    """测试用: 复位钩子(模拟 MCP/agent 进程未注入态)。"""
    global _session_checker
    _session_checker = None


def _identity(request: Request):
    return getattr(request.state, "identity", None)


def require_project_access(request: Request):
    """项目访问闸: 校验 request.state.identity 能否访问 X-Project-Id。

    返回 (identity, project_id) 供路由继续用。deny → PlatformError(ACCESS_DENIED) + 审计。
    platform_admin (§5.2 方案C): 身份级 bypass, 跨 org 放行 (非 advisory → 审计必记)。
    """
    identity = _identity(request)
    project_id = request.headers.get("X-Project-Id") or request.headers.get("x-project-id")
    cfg = load_config()
    # platform_admin bypass: 不进 scope×rank 阶梯, 跨 org 放行 (与 acl.all_projects 同范式)。
    if is_platform_admin(cfg, getattr(identity, "user_id", None)):
        decision = acl.AccessDecision(True, "platform admin: cross-org", advisory=False)
        audit.audit_access(_SERVICE, identity, project_id, decision)
        return identity, project_id
    # via=session(web 登录态): 走 web RBAC(注入的 checker); token/internal/passthrough 走 acl.can_access。
    # checker 未注入(MCP/agent 进程) → deny: session 身份不在非 web 侧放行(codex P2 #7)。
    if getattr(identity, "via", None) == "session":
        if _session_checker is None:
            decision = acl.AccessDecision(False, "session identity not authorized in this service")
        else:
            decision = _session_checker(identity, project_id)
        audit.audit_access(_SERVICE, identity, project_id, decision)
        if not decision.allowed:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "Current user cannot access this project.",
                                detail=decision.reason)
        return identity, project_id
    decision = acl.can_access(cfg, identity, project_id)
    audit.audit_access(_SERVICE, identity, project_id, decision)
    if not decision.allowed:
        raise PlatformError(ErrorCode.ACCESS_DENIED, "Current user cannot access this project.",
                            detail=decision.reason)
    return identity, project_id
