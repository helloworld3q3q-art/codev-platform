"""共享 token 签发逻辑 —— CLI(`gateway pg-token issue`)与 web(tokens 路由)共用, 不复制。

生成明文 token + sha256 hash 存 `PgTokenStore`, **明文只此一次返回**(库里只存 hash,见 token_store_pg)。
`org_id` 由调用方传 —— CLI 从 `--org`、web 从**认证 session.org_id**(绝不由 client body 传, 防越权,
见 rbac-multi-org-membership-model)。本模块只管"生成+hash+存", 不碰鉴权(由各调用方在自己层守)。
"""
from __future__ import annotations

import secrets
import time
from typing import Any


def issue_token(
    store: Any, user_id: str, org_id: str, *,
    projects: Any = None, label: str | None = None, ttl_seconds: int | None = None,
) -> tuple[str, float | None]:
    """生成明文 token + 存 hash, 返 `(明文 token, 过期戳 or None)`。**明文只此一次可得**。

    - `store`: `PgTokenStore`(或兼容 `.issue(token_hash, user_id, org_id, *, projects, label, expires_at)`)。
    - `projects`: `"*"` | `list[str]` | `None`(None = 无项目权,安全默认)。
    - `ttl_seconds`: `None` = 永久;否则过期戳 = now + ttl。
    """
    # 防御纵深: org_id/user_id 非空(org_id 须来自认证身份, 空串落库会让 token 绑到空 org → 越权面)。
    # 真正的"org_id 取 session 非 client body"硬护栏在 web 路由层, 此处只兜底拦空。
    if not user_id or not org_id:
        raise ValueError("issue_token: user_id 与 org_id 均不可为空 (org_id 须取自认证身份)")
    from codev_platform.gateway.auth import token_hash
    tok = secrets.token_urlsafe(32)
    exp = time.time() + ttl_seconds if ttl_seconds is not None else None
    store.issue(token_hash(tok), user_id, org_id,
                projects=projects, label=label, expires_at=exp)
    return tok, exp
