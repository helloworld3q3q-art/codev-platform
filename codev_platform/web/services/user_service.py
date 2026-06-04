"""User service —— profile/list/create/detail/update/status/password/roles/selections 编排
(plan §三 / §十五 Users)。

HTTP routes 不直接访问 store, 必经本 service 保权限/唯一性/审计边界。
- 唯一键 username; 密码只存 hash (security.passwords.hash_password), 明文绝不落库/日志。
- 禁用用户 → session_store.revoke_user(username) 使其会话失效 (plan §十五 规则)。
- org_admin 越权护栏: 非 platform_admin 的操作者只能管理 caller 自己 org 的用户。
- 角色变更 / 禁用 → core.audit 留痕 (plan §十五 "用户角色变更必须审计")。
错误统一抛 core.errors.PlatformError, app_factory 统一异常处理器转 envelope。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.web.domain.accounts import (
    STATUS_ACTIVE,
    STATUS_DISABLED,
    OrgMember,
    User,
)
from codev_platform.web.domain.enums import MemberRoleEnum, UserStatusEnum
from codev_platform.web.repositories.account_store import (
    get_member_store,
    get_user_store,
)
from codev_platform.web.schemas.users import (
    UserActionResult,
    UserItem,
    UserSelectionItem,
)
from codev_platform.web.security.passwords import hash_password
from codev_platform.web.security.sessions import session_store

_VALID_STATUS = {e.enum_value for e in UserStatusEnum}
_VALID_ROLE = {e.enum_value for e in MemberRoleEnum}


class UserService:
    def profile(self, username: str) -> UserItem:
        """当前登录用户信息。会话存在但用户记录缺失 → PROJECT_UNKNOWN 兜底。"""
        user = get_user_store().get(username)
        if user is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"user not found: {username}")
        return self._to_item(user)

    def list_users(self, *, org_id: str, offset: int, limit: int) -> tuple[list[UserItem], int]:
        """分页列出某 org 用户 (org_id 由路由层用 caller 的 org / 越权护栏决定)。"""
        rows = sorted(get_user_store().list(org_id=org_id), key=lambda u: u.username)
        total = len(rows)
        items = [self._to_item(u) for u in rows[offset:offset + limit]]
        return items, total

    def get_detail(self, *, username: str, caller_org_id: str, caller_is_admin: bool) -> UserItem:
        user = self._require_user(username)
        self._guard_same_org(user, caller_org_id, caller_is_admin)
        return self._to_item(user)

    def create_user(self, *, username: str, password: str, org_id: str,
                    display_name: str | None, email: str | None, role: str | None,
                    caller_org_id: str, caller_is_admin: bool) -> UserActionResult:
        """创建用户。username 唯一 (重复 → INVALID_PARAMS); 密码立即 hash。"""
        username = username.strip()
        org_id = org_id.strip()
        if not username or not org_id:
            raise PlatformError(ErrorCode.INVALID_PARAMS, "username / orgId 不能为空")
        # 越权护栏: 非 platform_admin 只能在自己 org 建用户。
        if not caller_is_admin and org_id != caller_org_id:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "org_admin 不能跨组织创建用户")
        if get_user_store().exists(username):
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"user already exists: {username}")
        role = self._coerce_role(role) if role else None
        user = User(
            username=username, password_hash=hash_password(password), org_id=org_id,
            status=STATUS_ACTIVE, display_name=display_name or "", email=email or "",
        )
        get_user_store().create(user)
        if role:
            get_member_store().upsert(OrgMember(org_id=org_id, username=username, role=role))
        return UserActionResult(username=username, status=user.status)

    def update_user(self, *, username: str, display_name: str | None, email: str | None,
                    caller_org_id: str, caller_is_admin: bool) -> UserActionResult:
        """更新资料 (不碰密码/状态/角色)。"""
        user = self._require_user(username)
        self._guard_same_org(user, caller_org_id, caller_is_admin)
        updated = User(
            username=user.username, password_hash=user.password_hash, org_id=user.org_id,
            status=user.status,
            display_name=display_name if display_name is not None else user.display_name,
            email=email if email is not None else user.email,
        )
        get_user_store().upsert(updated)
        return UserActionResult(username=username, status=updated.status)

    def set_status(self, *, username: str, status: str, caller_org_id: str,
                   caller_is_admin: bool, actor: str) -> UserActionResult:
        """启用 / 禁用。禁用 → 撤销其所有会话 + 审计。"""
        user = self._require_user(username)
        self._guard_same_org(user, caller_org_id, caller_is_admin)
        status = self._coerce_status(status)
        updated = User(
            username=user.username, password_hash=user.password_hash, org_id=user.org_id,
            status=status, display_name=user.display_name, email=user.email,
        )
        get_user_store().upsert(updated)
        if status == STATUS_DISABLED:
            session_store.revoke_user(username)  # plan §十五: 禁用必须令 token/session 失效
            self._audit(actor, "user.disable", username, {"org_id": user.org_id})
        else:
            self._audit(actor, "user.enable", username, {"org_id": user.org_id})
        return UserActionResult(username=username, status=status)

    def reset_password(self, *, username: str, new_password: str, caller_org_id: str,
                       caller_is_admin: bool, actor: str) -> UserActionResult:
        """重置 / 生成初始密码。新明文 hash 后落库, 明文不日志。"""
        user = self._require_user(username)
        self._guard_same_org(user, caller_org_id, caller_is_admin)
        updated = User(
            username=user.username, password_hash=hash_password(new_password), org_id=user.org_id,
            status=user.status, display_name=user.display_name, email=user.email,
        )
        get_user_store().upsert(updated)
        self._audit(actor, "user.password_reset", username, {"org_id": user.org_id})
        return UserActionResult(username=username, status=updated.status)

    def set_roles(self, *, username: str, org_id: str, role: str, caller_org_id: str,
                  caller_is_admin: bool, actor: str) -> UserActionResult:
        """变更用户在某 org 的成员角色 (须审计)。"""
        user = self._require_user(username)
        org_id = org_id.strip()
        if not caller_is_admin and org_id != caller_org_id:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "org_admin 不能跨组织改角色")
        role = self._coerce_role(role)
        get_member_store().upsert(OrgMember(org_id=org_id, username=username, role=role))
        self._audit(actor, "user.roles", username, {"org_id": org_id, "role": role})
        return UserActionResult(username=username, status=user.status)

    def selections(self, *, org_id: str) -> list[UserSelectionItem]:
        rows = sorted(get_user_store().list(org_id=org_id), key=lambda u: u.username)
        return [UserSelectionItem(label=u.display_name or u.username, value=u.username)
                for u in rows]

    # ---- helpers ----

    def _require_user(self, username: str) -> User:
        user = get_user_store().get(username)
        if user is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"user not found: {username}")
        return user

    @staticmethod
    def _guard_same_org(user: User, caller_org_id: str, caller_is_admin: bool) -> None:
        """org_admin 越权护栏: 非 platform_admin 只能动自己 org 的用户。"""
        if not caller_is_admin and user.org_id != caller_org_id:
            raise PlatformError(ErrorCode.ACCESS_DENIED, "org_admin 不能管理其他组织用户")

    @staticmethod
    def _coerce_status(status: str) -> str:
        status = (status or "").strip()
        if status not in _VALID_STATUS:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"非法状态: {status}")
        return status

    @staticmethod
    def _coerce_role(role: str) -> str:
        role = (role or "").strip()
        if role not in _VALID_ROLE:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"非法角色: {role}")
        return role

    @staticmethod
    def _audit(actor: str, action: str, target: str, extra: dict) -> None:
        """写用户管理审计 (角色变更/禁用/重置密码 留痕, plan §十五 "必须审计")。

        core.audit.audit_access 形参面向"项目访问授权判定", 与 user 管理事件不贴合, 故
        这里就近写一条结构化 jsonl 到 data_root/audit/user_admin.jsonl (与 access.jsonl 并排,
        复用 core.audit 的目录约定)。只记身份标识 + 动作, 无密钥/明文 (security.md)。
        失败静默 —— 审计不得拖垮主流程。

        TODO(plan §十五): 待 core.audit 抽出通用 admin 事件 sink (PG/jsonl) 后切过去。
        """
        try:
            import datetime
            import json

            from codev_platform.core.audit import audit_log_path

            rec = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "service": "web", "actor": actor, "action": action,
                "target": target, **extra,
            }
            path = audit_log_path().with_name("user_admin.jsonl")
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — 审计写失败不得影响请求
            pass

    @staticmethod
    def _to_item(user: User) -> UserItem:
        # role 取该用户在自身 org 的成员角色 (account 表无 role, 真值在 OrgMember)。
        member = get_member_store().get(user.org_id, user.username)
        return UserItem(
            username=user.username, orgId=user.org_id,
            displayName=user.display_name or None, email=user.email or None,
            status=user.status,
            role=member.role if member else None,
        )
