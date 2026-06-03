"""Web projects 授权 —— org 隔离 + 逐项目角色。

需求(用户澄清): 不同组织成员只管自己组织的项目; 组织内成员对不同项目还有逐项目权限限制。

权限判定**单一真值源** = core.rbac.role_allows + Membership(不另造一套)。Membership 两路后端:
  - prod(PG): agent.deps.get_rbac_store().fetch_membership —— 读 project_access 表的逐项目 role;
  - dev/内存: web member_store 的 OrgMember(org 级 role + project_roles 字典)组装。
两路都产出 core.rbac.Membership, 上层只认它。

安全默认: 无 org / 无 user / 无角色 → Membership 空 → role_allows 恒 False(deny)。
platform_admin 与 org admin 覆盖本 org 全部项目(跨项目 bypass, 但不跨 org —— org 隔离由
service 层用 project 的 org_id 二次校验, 见 project_service._authorize_project)。
"""
from __future__ import annotations

from codev_platform.core.config import load_config
from codev_platform.core.platform_admin import is_platform_admin
from codev_platform.core.rbac import Membership, role_allows
from codev_platform.web.repositories.account_store import get_member_store
from codev_platform.web.security.sessions import Session


def resolve_membership(org_id: str | None, username: str | None,
                       project_id: str | None) -> Membership:
    """组装 (org, user, project) 的角色快照。PG RBAC 优先, 缺则回内存 OrgMember。"""
    if not org_id or not username:
        return Membership()
    store = _pg_rbac_store()
    if store is not None:
        try:
            return store.fetch_membership(org_id, username, project_id)
        except Exception:  # noqa: BLE001 — PG 故障不得使 web 裸奔; 落回内存 deny-by-default
            pass
    member = get_member_store().get(org_id, username)
    if member is None:
        return Membership()
    project_role = None
    if project_id:
        project_role = (getattr(member, "project_roles", None) or {}).get(project_id)
    return Membership(org_role=member.role, project_role=project_role)


def _pg_rbac_store():
    """prod 的 PG RBAC store(project_access 真值源); 缺 psycopg/dsn → None。"""
    try:
        from codev_platform.agent.deps import get_rbac_store
        return get_rbac_store()
    except Exception:  # noqa: BLE001
        return None


def is_org_admin(sess: Session) -> bool:
    """platform_admin 或本 org admin —— 覆盖本组织全部项目。"""
    if is_platform_admin(load_config(), sess.username):
        return True
    m = resolve_membership(sess.org_id, sess.username, None)
    return role_allows(m.org_role, "admin")


def resolve_session_roles(sess: Session) -> list[str]:
    """会话用户的可信角色清单(供前端可见性, 不是鉴权闸)。

    角色**只从后端可信源算**(platform_admin 白名单 + org membership), 绝不信 client。
    输出去重保序: platform_admin → org 级角色(admin|member|viewer)。前端 isAdminRole 消费
    platform_admin/admin 做菜单显隐; 真正鉴权仍是后端 require_org_role / scope decision。
    """
    roles: list[str] = []
    if is_platform_admin(load_config(), sess.username):
        roles.append("platform_admin")
    m = resolve_membership(sess.org_id, sess.username, None)
    if m.org_role is not None and m.org_role not in roles:
        roles.append(m.org_role)
    return roles


def can_access_project(sess: Session, project_id: str, action: str) -> bool:
    """session 用户对 project_id 是否覆盖 action(read|write|admin)。org/platform admin bypass。"""
    if is_org_admin(sess):
        return True
    m = resolve_membership(sess.org_id, sess.username, project_id)
    return role_allows(m.project_role, action)
