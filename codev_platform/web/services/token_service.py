"""Token service —— PG agent token 的 web 签发 / 列出 / 吊销编排。

HTTP routes 不直接访问 store, 必经本 service 守授权边界。复用单一真值源, 不复制逻辑:
- 签发逻辑 / 时长 / 项目白名单解析  → gateway.token_issue(issue_token / parse_duration / coerce_projects)
- store 构造(config memory.pg_dsn)  → gateway.token_store_pg.build_token_store
- "目标用户须本 org 成员" 护栏        → UserService._guard_org_member(账户成员表真值源)

安全红线(rbac-multi-org-membership-model):
- org_id 由**路由取 session.org_id** 传入, 绝不由 client body —— 防越权签发到任意 org。
- 签发: 目标用户须是 caller org 成员(_guard_org_member); platform_admin bypass。
- 列出 / 吊销: org_admin **只见 / 只吊销本 org** 的 token —— 防凭 hash 前缀跨 org 越权吊销。
- 明文 token 只在 issue 一次返回, 库里只存 hash(issue_token / PgTokenStore 已守, security.md)。
"""
from __future__ import annotations

from typing import Any

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.gateway.token_issue import coerce_projects, issue_token, parse_duration
from codev_platform.web.repositories.account_store import get_user_store
from codev_platform.web.schemas.tokens import (
    TokenActionResult,
    TokenIssueResult,
    TokenItem,
)
from codev_platform.web.services.user_service import UserService


class TokenService:
    """PG token 编排。store 默认惰性按 config 构造; 测试注入 PgTokenStore(engine=sqlite) exercise 真 SQL。"""

    def __init__(self, store: Any = None) -> None:
        self._store = store

    def issue(self, *, target_user: str, projects: str | None, label: str | None,
              expires: str | None, org_id: str,
              caller_org_id: str, caller_is_admin: bool) -> TokenIssueResult:
        """签发 token。org_id 由路由取 session.org_id(非 client)。目标用户须本 org 成员。"""
        user = self._require_user(target_user)
        UserService._guard_org_member(user, caller_org_id, caller_is_admin)
        try:
            ttl = parse_duration(expires)
        except ValueError as exc:
            raise PlatformError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
        proj = coerce_projects(projects)
        tok, exp = issue_token(self._store_or_build(), target_user, org_id,
                               projects=proj, label=label, ttl_seconds=ttl)
        return TokenIssueResult(token=tok, userId=target_user, orgId=org_id,
                                projects=proj, expiresAt=exp)

    def list_tokens(self, *, caller_org_id: str, caller_is_admin: bool,
                    target_user: str | None = None) -> list[TokenItem]:
        """列 token(不含明文)。org_admin 只见本 org —— 服务层按 org 过滤(store 无 org 维度查询)。"""
        rows = self._store_or_build().list_tokens(target_user or None)
        if not caller_is_admin:
            rows = [r for r in rows if r.get("org_id") == caller_org_id]
        return [self._to_item(r) for r in rows]

    def revoke(self, *, token_hash_prefix: str,
               caller_org_id: str, caller_is_admin: bool) -> TokenActionResult:
        """按 hash 前缀吊销。先校验命中的 active token 都属本 org, 再吊销(防跨 org 越权)。"""
        store = self._store_or_build()
        victims = [r for r in store.list_tokens()
                   if r.get("status") == "ACTIVE" and str(r.get("token_hash", "")).startswith(token_hash_prefix)]
        if not victims:
            return TokenActionResult(revoked=0)
        if not caller_is_admin:
            for r in victims:
                if r.get("org_id") != caller_org_id:
                    raise PlatformError(ErrorCode.ACCESS_DENIED, "org_admin 不能吊销其他组织的 token")
        return TokenActionResult(revoked=store.revoke(token_hash_prefix))

    # ---- helpers ----

    def _store_or_build(self):
        """惰性构造 PG token store(config memory.pg_dsn); dsn 未配 / psycopg 缺 → DEPENDENCY_MISSING。"""
        if self._store is None:
            from codev_platform.gateway.token_store_pg import build_token_store
            try:
                self._store = build_token_store()
            except Exception as exc:  # noqa: BLE001 — dsn 未配 / 缺 psycopg / dsn 坏, 统一转友好 503
                raise PlatformError(
                    ErrorCode.DEPENDENCY_MISSING,
                    f"PG token 后端不可用 (需 memory.pg_dsn + psycopg): {exc}",
                ) from exc
        return self._store

    @staticmethod
    def _require_user(username: str):
        user = get_user_store().get(username)
        if user is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"user not found: {username}")
        return user

    @staticmethod
    def _to_item(r: dict) -> TokenItem:
        return TokenItem(
            userId=r["user_id"], orgId=r["org_id"], projects=r.get("projects"),
            label=r.get("label"), status=r.get("status", "ACTIVE"),
            expiresAt=r.get("expires_at"), tokenHashPrefix=str(r.get("token_hash", ""))[:12],
        )
