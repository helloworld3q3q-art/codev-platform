"""会话感知认证器 —— web 控制台用 session token 鉴权, 与 gateway 静态 token 并存。

双轨收口 (deep-audit-2026-06-03-review.md 决策2): token 模式下 gateway AuthMiddleware 默认
只认 gateway 静态 token, 会把只持 **web 登录 session token** 的请求在 route 之前就 401。
本认证器包一层:
  1. 先认 session_store 的 access token (web 登录签发) → 命中即放行 (给 via="session" 身份);
  2. 未命中再委托内层 gateway 认证器 (token / passthrough), 保持 MCP / 程序化访问的 gateway
     token 通道不变。

放行只是过中间件闸 —— 真正的逐项目授权仍由 web 路由的 current_session + project_service
的 org 隔离/逐项目 RBAC 判 (本认证器给 all_projects=False, 不靠 gateway ACL 放行)。

层次: 依赖 web.security.sessions, 故放 web 层 (gateway 是下层, 不反向依赖 web)。
"""
from __future__ import annotations

from collections.abc import Mapping

from codev_platform.gateway.auth import Authenticator, Identity
from codev_platform.web.security.sessions import session_store


def _bearer(headers: Mapping[str, str]) -> str | None:
    raw = None
    if hasattr(headers, "get"):
        raw = headers.get("Authorization") or headers.get("authorization")
    if raw and raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return None


class SessionAwareAuthenticator:
    """先 session token, 未命中再委托内层 gateway 认证器 (策略接口, 实现 Authenticator)。"""

    def __init__(self, inner: Authenticator) -> None:
        self._inner = inner

    def authenticate(self, headers: Mapping[str, str]) -> Identity:
        tok = _bearer(headers)
        if tok:
            sess = session_store.resolve(tok)
            if sess is not None:
                # session 身份仅用于过中间件; 逐项目授权由 current_session + service 闸判。
                return Identity(user_id=sess.username, org_id=sess.org_id, via="session")
        return self._inner.authenticate(headers)
