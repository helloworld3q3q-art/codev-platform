"""账户存储 —— Org / User / OrgMember 内存实现 (plan §十一 / §十五)。

共享数据层 (Auth/Orgs/Users 三个垂直片都用), 故放地基统一定义, 防各片各造一套冲突。
内存实现便于测试; PG 实现 (按现有 *_store_pg 范式) 留 TODO, 接口不变。
密码只存 hash (User.password_hash)。read/write 方法同表, service 决定何时读写。
"""
from __future__ import annotations

import logging

from codev_platform.web.domain.accounts import Org, OrgMember, User

logger = logging.getLogger(__name__)


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


# 进程内内存单实例 (默认后端)。bind_account_stores 可换成 PG-backed, 调用方经 getter 无感。
org_store = OrgStore()
user_store = UserStore()
member_store = MemberStore()

# 活动绑定 (默认内存; bind_account_stores 按 config 切 PG)。服务经 getter 取, 不直接 import 单例。
_active: dict[str, object] = {"org": org_store, "user": user_store, "member": member_store}


def get_org_store():
    return _active["org"]


def get_user_store():
    return _active["user"]


def get_member_store():
    return _active["member"]


def bind_account_stores(cfg: dict | None = None) -> str:
    """按 config 选账户存储后端 —— `memory.pg_dsn` + psycopg 可用 → PG, 否则内存 (优雅回退)。

    返回选中的后端名 ("pg" | "memory"), 供启动日志/测试断言。幂等: 重复调用按当前 cfg 重绑。
    """
    from codev_platform.core.config import get as _cfg_get

    dsn = _cfg_get(cfg or {}, "memory.pg_dsn", None)
    if not dsn:
        _active.update(org=org_store, user=user_store, member=member_store)
        return "memory"
    try:
        from codev_platform.web.repositories.account_store_pg import (
            PgMemberStore,
            PgOrgStore,
            PgUserStore,
        )
        # 实例化即触发 psycopg_pool import (ConnectionPool open=False 不连库); 缺 psycopg 在此抛。
        pg = {"org": PgOrgStore(dsn), "user": PgUserStore(dsn), "member": PgMemberStore(dsn)}
    except ImportError:
        # P0-3: prod 配了 PG dsn 却缺 psycopg → fail-fast, 不静默回退内存 (防运维以为用 PG 实际
        # 走内存, 账户/RBAC 状态重启即丢)。与下方 Exception 分支同策略。
        mode = _cfg_get(cfg or {}, "deployment.mode", "dev")
        if mode == "prod":
            raise
        # dev / 平台 venv 常态: psycopg 未装 → 回退内存
        _active.update(org=org_store, user=user_store, member=member_store)
        return "memory"
    except Exception as exc:  # noqa: BLE001
        mode = _cfg_get(cfg or {}, "deployment.mode", "dev")
        if mode == "prod":
            raise  # prod 配了 PG 却初始化失败 → fail-fast, 不静默降级
        logger.warning("PG account store 初始化失败, dev 回退内存: %r", exc)
        _active.update(org=org_store, user=user_store, member=member_store)
        return "memory"
    _active.update(pg)
    return "pg"


def reset_account_stores() -> None:
    """清空共享内存账户表 + 重置绑定到内存 (测试隔离用; PG 实现无此操作)。"""
    org_store._by_code.clear()
    user_store._by_username.clear()
    member_store._by_key.clear()
    _active.update(org=org_store, user=user_store, member=member_store)
