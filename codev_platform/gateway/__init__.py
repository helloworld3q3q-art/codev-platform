"""平台 HTTP 网关层 —— 认证 + 统一请求拦截。

独立模块,只依赖 codev_platform.core(identity / config),**不耦合**进 agent.routes /
chroma / graph 业务。所有 HTTP 入口(agent 服务、chroma daemon、将来的 MCP-SSE 端点)
复用这一个网关:在请求入口统一认证 → 解析 (org_id, user_id) 上下文 → 挂到 request.state。

auth 可插拔(config gateway.auth_mode):
  - passthrough(单人/开发期默认):信任 X-User-Id / X-Org-Id 明文头,只做身份解析+上下文,
    **不验签**(挡误操作,挡不了冒充)。
  - token(多人/对外,M6):验 Bearer token → (org,user),token 表走 config/PG。
真鉴权(token 验签 + 用户库)是 M6 增量;本层先把"统一拦截 + 身份上下文"的接缝立起来。
"""
from codev_platform.gateway.auth import (
    Authenticator,
    Identity,
    PassthroughAuthenticator,
    TokenAuthenticator,
    Unauthorized,
    build_authenticator,
    deploy_policy_error,
    multi_user_policy_error,
)
from codev_platform.gateway.middleware import (
    AuthMiddleware,
    RateLimitMiddleware,
    maybe_rate_limit_middleware,
)

__all__ = [
    "Authenticator",
    "Identity",
    "PassthroughAuthenticator",
    "TokenAuthenticator",
    "Unauthorized",
    "build_authenticator",
    "deploy_policy_error",
    "multi_user_policy_error",
    "AuthMiddleware",
    "RateLimitMiddleware",
    "maybe_rate_limit_middleware",
]
