"""Org service —— 组织 CRUD + 成员管理的业务编排 (plan §三 / §十五 Orgs)。

HTTP routes 不直接访问 store, 必须经本 service 保持幂等/校验边界。授权 (平台超管 /
org_admin) 在路由层 Depends 完成, 本 service 只做数据编排与值域校验。
错误统一抛 core.errors.PlatformError → app_factory 统一异常处理器转 envelope。
状态/角色值域对齐 web/domain/enums.py 真值源 (cross-layer-enum-consistency), 非法 →
INVALID_PARAMS; 组织不存在 → PROJECT_UNKNOWN (404 复用, 无独立 NOT_FOUND code)。
"""
from __future__ import annotations

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.project_id import ProjectIdError, validate
from codev_platform.web.domain.accounts import Org, OrgMember
from codev_platform.web.domain.enums import MemberRoleEnum, OrgStatusEnum
from codev_platform.web.repositories.account_store import (
    get_member_store,
    get_org_store,
    get_user_store,
)
from codev_platform.web.schemas.orgs import (
    MemberActionResult,
    MemberItem,
    OrgActionResult,
    OrgItem,
    OrgSelectionItem,
)

_VALID_STATUS = {e.value for e in OrgStatusEnum}
_VALID_ROLE = {e.value for e in MemberRoleEnum}


class OrgService:
    def __init__(self, orgs=None, members=None, users=None) -> None:
        # 经 getter 取活动绑定 (内存或 PG, 见 bind_account_stores); 测试可显式注入。
        self._orgs = orgs if orgs is not None else get_org_store()
        self._members = members if members is not None else get_member_store()
        self._users = users if users is not None else get_user_store()

    # ---- 组织 ----

    def list_orgs(self, *, offset: int, limit: int) -> tuple[list[OrgItem], int]:
        rows = sorted(self._orgs.list(), key=lambda o: o.code)
        total = len(rows)
        return [self._to_item(o) for o in rows[offset:offset + limit]], total

    def get_detail(self, code: str) -> OrgItem:
        return self._to_item(self._require(code))

    def selections(self) -> list[OrgSelectionItem]:
        """下拉选择: 仅 ACTIVE 组织, 按 code 排序。"""
        rows = sorted(self._orgs.list(), key=lambda o: o.code)
        return [
            OrgSelectionItem(code=o.code, name=o.name, status=o.status)
            for o in rows
            if o.status == OrgStatusEnum.ACTIVE.value
        ]

    def create_org(self, *, code: str, name: str, description: str | None) -> OrgActionResult:
        """创建组织 (幂等: 重复 code → INVALID_PARAMS)。"""
        code = self._validate_code(code)
        if self._orgs.exists(code):
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"org already exists: {code}")
        org = self._orgs.create(Org(code=code, name=name, description=description or ""))
        return OrgActionResult(code=org.code, status=org.status)

    def update_org(self, *, code: str, name: str | None,
                   description: str | None) -> OrgActionResult:
        """更新名称 / 描述 (code 不可改; 状态走 set_status)。"""
        org = self._require(code)
        updated = Org(
            code=org.code,
            name=name if name is not None else org.name,
            status=org.status,
            description=description if description is not None else org.description,
        )
        self._orgs.upsert(updated)
        return OrgActionResult(code=updated.code, status=updated.status)

    def set_status(self, *, code: str, status: str) -> OrgActionResult:
        """启用 / 禁用 (第一版只置状态, 不物理删)。"""
        if status not in _VALID_STATUS:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"invalid org status: {status}")
        org = self._require(code)
        updated = Org(code=org.code, name=org.name, status=status, description=org.description)
        self._orgs.upsert(updated)
        return OrgActionResult(code=updated.code, status=updated.status)

    # ---- 成员 ----

    def list_members(self, code: str, *, offset: int, limit: int) -> tuple[list[MemberItem], int]:
        self._require(code)
        rows = sorted(self._members.list_org(code), key=lambda m: m.username)
        total = len(rows)
        return [self._to_member(m) for m in rows[offset:offset + limit]], total

    def add_member(self, *, code: str, username: str, role: str) -> MemberActionResult:
        """加成员 (幂等 upsert)。role 非法 → INVALID_PARAMS; user 不存在 → PROJECT_UNKNOWN。"""
        self._require(code)
        role = self._validate_role(role)
        # P1-2(backend-deep): 校验 user 存在 —— add_member = 加已有用户为成员, 防幽灵成员 / orphan
        # membership(当前无 invitation 流程; RBAC / 成员列表 / 审计归属都靠真实 user)。
        if not self._users.exists(username):
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"user not found: {username}")
        m = self._members.upsert(OrgMember(org_id=code, username=username, role=role))
        return MemberActionResult(orgId=m.org_id, username=m.username, role=m.role)

    def remove_member(self, *, code: str, username: str) -> MemberActionResult:
        self._require(code)
        self._members.remove(code, username)
        return MemberActionResult(orgId=code, username=username, removed=True)

    def set_member_role(self, *, code: str, username: str, role: str) -> MemberActionResult:
        """改成员角色。成员不存在 → PROJECT_UNKNOWN; role 非法 → INVALID_PARAMS。"""
        self._require(code)
        role = self._validate_role(role)
        existing = self._members.get(code, username)
        if existing is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"member not found: {username}")
        m = self._members.upsert(OrgMember(
            org_id=code, username=username, role=role,
            project_roles=existing.project_roles,
        ))
        return MemberActionResult(orgId=m.org_id, username=m.username, role=m.role)

    # ---- helpers ----

    def _require(self, code: str) -> Org:
        code = self._validate_code(code)
        org = self._orgs.get(code)
        if org is None:
            raise PlatformError(ErrorCode.PROJECT_UNKNOWN, f"org not found: {code}")
        return org

    @staticmethod
    def _to_item(o: Org) -> OrgItem:
        return OrgItem(code=o.code, name=o.name, status=o.status, description=o.description)

    @staticmethod
    def _to_member(m: OrgMember) -> MemberItem:
        return MemberItem(orgId=m.org_id, username=m.username, role=m.role)

    @staticmethod
    def _validate_code(code: str) -> str:
        try:
            return validate(code)
        except ProjectIdError as exc:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"invalid org code: {code}",
                                detail=str(exc)) from exc

    @staticmethod
    def _validate_role(role: str) -> str:
        if role not in _VALID_ROLE:
            raise PlatformError(ErrorCode.INVALID_PARAMS, f"invalid member role: {role}")
        return role
