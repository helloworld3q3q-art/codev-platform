"""routes 层共享小工具(DRY)—— 身份解析。

会话 / 后续只读路由都需要 (user_id, org_id)。抽出公共解析,语义与 chat.py 一致:
优先用 gateway 中间件认证后写入 request.state.identity 的可信身份,没挂 gateway(dev 单机)
才回退裸 header 解析(防 token 鉴权下持合法 token 者伪造他人 user/org)。
"""
from __future__ import annotations

from fastapi import Request

from codev_platform.core import identity


def resolve_user_org(request: Request) -> tuple[str, str]:
    """(user_id, org_id)。非法 header 由 identity.resolve_* 抛 ValueError,调用方转 400。"""
    ident = getattr(request.state, "identity", None)
    if ident is not None:
        return ident.user_id, ident.org_id
    return (
        identity.resolve_from_request(request.headers),
        identity.resolve_org_from_request(request.headers),
    )
