"""账户存储 —— Org / User / OrgMember 内存实现 (plan §十一 / §十五)。

共享数据层 (Auth/Orgs/Users 三个垂直片都用), 故放地基统一定义, 防各片各造一套冲突。
内存实现便于测试; PG 实现 (按现有 *_store_pg 范式) 留 TODO, 接口不变。
密码只存 hash (User.password_hash)。read/write 方法同表, service 决定何时读写。
"""
from __future__ import annotations

from codev_platform.web.domain.accounts import Org, OrgMember, User


class OrgStore:
    def __init__(self) -> None:
        self._by_code: dict[str, Org] = {}

    def get(self, code: str) -> Org | None:
        return self._by_code.get(code)

    def exists(self, code: str) -> bool:
        return code in self._by_code

    def list(self) -> list[Org]:
        return list(self._by_code.values())

    def create(self, org: Org) -> Org:
        self._by_code[org.code] = org
        return org

    def upsert(self, org: Org) -> Org:
        self._by_code[org.code] = org
        return org


class UserStore:
    def __init__(self) -> None:
        self._by_username: dict[str, User] = {}

    def get(self, username: str) -> User | None:
        return self._by_username.get(username)

    def exists(self, username: str) -> bool:
        return username in self._by_username

    def list(self, org_id: str | None = None) -> list[User]:
        users = list(self._by_username.values())
        return [u for u in users if org_id is None or u.org_id == org_id]

    def create(self, user: User) -> User:
        self._by_username[user.username] = user
        return user

    def upsert(self, user: User) -> User:
        self._by_username[user.username] = user
        return user


class MemberStore:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], OrgMember] = {}  # (org_id, username) -> member

    def get(self, org_id: str, username: str) -> OrgMember | None:
        return self._by_key.get((org_id, username))

    def list_org(self, org_id: str) -> list[OrgMember]:
        return [m for (o, _), m in self._by_key.items() if o == org_id]

    def list_user(self, username: str) -> list[OrgMember]:
        return [m for (_, u), m in self._by_key.items() if u == username]

    def upsert(self, member: OrgMember) -> OrgMember:
        self._by_key[(member.org_id, member.username)] = member
        return member

    def remove(self, org_id: str, username: str) -> None:
        self._by_key.pop((org_id, username), None)


# 进程内单实例 (共享数据层)。PG 实现后改为注入 PG-backed store, 调用方无感。
org_store = OrgStore()
user_store = UserStore()
member_store = MemberStore()


def reset_account_stores() -> None:
    """清空共享内存账户表 (测试隔离用; 生产 PG 实现无此操作)。"""
    org_store._by_code.clear()
    user_store._by_username.clear()
    member_store._by_key.clear()
