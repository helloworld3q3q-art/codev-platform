"""SQLAlchemy Core 改写后的 RBAC/账户 store 真实逻辑覆盖(2026-06-03 迁移守护)。

平台 venv 无 psycopg/PG,旧测试只能走内存回退路径,**不 exercise 改写后的 SQL 表达式**。
本测试用 sqlite 内存 engine 注入,真实跑 select()/insert()/update()/delete() + upsert 方言分支
(sqlite on_conflict_do_update),覆盖:
  - PgOrgStore / PgUserStore / PgMemberStore 的 create/get/list/exists/upsert/remove + upsert 覆盖语义
  - RbacStore 的 add_org_member / grant_project / fetch_membership(org_role + project_role + 多来源取最高)

这是改写逻辑的唯一真实覆盖(不依赖真 PG)。
"""
from __future__ import annotations

import pytest

from codev_platform.web.domain.accounts import Org, OrgMember, User

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine  # noqa: E402

from codev_platform.agent.rbac_store_pg import RbacStore  # noqa: E402
from codev_platform.web.repositories.account_store_pg import (  # noqa: E402
    PgMemberStore,
    PgOrgStore,
    PgUserStore,
)


@pytest.fixture()
def engine():
    """单个 sqlite 内存 engine,所有 store 共享(同库同表语义)。"""
    eng = create_engine("sqlite://")
    yield eng
    eng.dispose()


# ---------------- 账户 store ----------------

def test_org_store_create_get_list_upsert(engine):
    store = PgOrgStore(engine=engine)
    assert store.get("acme") is None
    assert store.exists("acme") is False

    created = store.create(Org(code="acme", name="Acme", status="ACTIVE"))
    assert created.code == "acme"
    got = store.get("acme")
    assert got == Org(code="acme", name="Acme", status="ACTIVE")
    assert store.exists("acme") is True

    # upsert 覆盖语义:同 org_id 改 name/status
    store.upsert(Org(code="acme", name="Acme2", status="DISABLED"))
    got2 = store.get("acme")
    assert got2.name == "Acme2" and got2.status == "DISABLED"

    store.create(Org(code="beta", name="Beta", status="ACTIVE"))
    codes = [o.code for o in store.list()]
    assert codes == ["acme", "beta"]  # ORDER BY org_id


def test_user_store_get_org_id_from_membership(engine):
    org = PgOrgStore(engine=engine)
    usr = PgUserStore(engine=engine)
    mem = PgMemberStore(engine=engine)
    org.create(Org(code="acme", name="Acme", status="ACTIVE"))

    usr.create(User(username="alice", password_hash="h1", org_id="acme",
                    status="ACTIVE", display_name="Alice", email="a@x"))
    # 无 membership → org_id 回退 'default'
    assert usr.get("alice").org_id == "default"
    mem.upsert(OrgMember(org_id="acme", username="alice", role="member"))
    # 有 membership → org_id 取该 org
    got = usr.get("alice")
    assert got.org_id == "acme" and got.password_hash == "h1" and got.email == "a@x"
    assert usr.exists("alice") is True
    assert usr.exists("ghost") is False

    # upsert 覆盖语义:同 username 改字段
    usr.upsert(User(username="alice", password_hash="h2", org_id="acme",
                    status="DISABLED", display_name="Alice2", email="a2@x"))
    got2 = usr.get("alice")
    assert got2.password_hash == "h2" and got2.status == "DISABLED" and got2.display_name == "Alice2"


def test_user_store_set_password_targeted_update(engine):
    # set_password 只改 password_hash, 保住 display_name/email/status(不全量覆盖, 不造 fork)。
    usr = PgUserStore(engine=engine)
    usr.create(User(username="carol", password_hash="old", org_id="acme",
                    status="ACTIVE", display_name="Carol", email="c@x"))
    assert usr.set_password("carol", "newh") is True
    got = usr.get("carol")
    assert got.password_hash == "newh"
    assert got.display_name == "Carol" and got.email == "c@x" and got.status == "ACTIVE"  # 保住
    assert usr.set_password("ghost", "h") is False   # 不存在 → False(调用方提示先 add-user)


def test_user_store_list_filtered_by_org(engine):
    org = PgOrgStore(engine=engine)
    usr = PgUserStore(engine=engine)
    mem = PgMemberStore(engine=engine)
    org.create(Org(code="acme", name="Acme", status="ACTIVE"))
    org.create(Org(code="beta", name="Beta", status="ACTIVE"))
    usr.create(User(username="alice", password_hash="h", org_id="acme"))
    usr.create(User(username="bob", password_hash="h", org_id="beta"))
    mem.upsert(OrgMember(org_id="acme", username="alice", role="member"))
    mem.upsert(OrgMember(org_id="beta", username="bob", role="member"))

    assert [u.username for u in usr.list()] == ["alice", "bob"]            # 全量,ORDER BY user_id
    assert [u.username for u in usr.list("acme")] == ["alice"]             # JOIN org_members 过滤
    assert [u.username for u in usr.list("beta")] == ["bob"]


def test_member_store_crud(engine):
    org = PgOrgStore(engine=engine)
    usr = PgUserStore(engine=engine)
    mem = PgMemberStore(engine=engine)
    org.create(Org(code="acme", name="Acme", status="ACTIVE"))
    usr.create(User(username="alice", password_hash="h", org_id="acme"))
    usr.create(User(username="bob", password_hash="h", org_id="acme"))

    mem.upsert(OrgMember(org_id="acme", username="alice", role="member"))
    mem.upsert(OrgMember(org_id="acme", username="bob", role="viewer"))
    assert mem.get("acme", "alice") == OrgMember(org_id="acme", username="alice", role="member")
    assert [m.username for m in mem.list_org("acme")] == ["alice", "bob"]  # ORDER BY user_id
    assert [m.org_id for m in mem.list_user("alice")] == ["acme"]

    # upsert 覆盖语义:角色提升
    mem.upsert(OrgMember(org_id="acme", username="alice", role="admin"))
    assert mem.get("acme", "alice").role == "admin"

    mem.remove("acme", "alice")
    assert mem.get("acme", "alice") is None
    assert [m.username for m in mem.list_org("acme")] == ["bob"]


# ---------------- RBAC store ----------------

def _seed_rbac(rb: RbacStore) -> None:
    rb.add_org("acme", "Acme")
    rb.add_org("other", "Other")
    rb.add_user("alice", "Alice")
    rb.add_org_member("acme", "alice", role="member")
    rb.add_team("teamA", "acme", "Team A")
    rb.add_team_member("teamA", "alice", role="admin")
    rb.upsert_project("proj1", "acme", "P1")   # user 直授
    rb.upsert_project("proj2", "acme", "P2")   # team 授权
    rb.grant_project("proj1", "alice", "user", role="member")
    rb.grant_project("proj2", "teamA", "team", role="viewer")


def test_rbac_fetch_membership_user_grant(engine):
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    m = rb.fetch_membership("acme", "alice", "proj1")
    assert m.org_role == "member"
    assert m.teams == (("teamA", "admin"),)
    assert m.project_role == "member"


def test_rbac_fetch_membership_team_grant(engine):
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    m = rb.fetch_membership("acme", "alice", "proj2")
    # alice 无 proj2 user 直授,但其 team teamA 被授 viewer
    assert m.project_role == "viewer"


def test_rbac_highest_role_multi_source(engine):
    """user 直授 viewer + team 授 admin 同一 project → 取最高 admin。"""
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    rb.grant_project("proj1", "teamA", "team", role="admin")  # proj1 再经 team 授 admin
    m = rb.fetch_membership("acme", "alice", "proj1")
    assert m.project_role == "admin"  # user=member 与 team=admin 取最高


def test_rbac_cross_org_isolation(engine):
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    m = rb.fetch_membership("other", "alice", "proj1")
    assert m.org_role is None and m.teams == () and m.project_role is None


def test_rbac_upsert_idempotent(engine):
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    # 幂等复跑 + 角色更新(on_conflict_do_update)
    rb.add_org_member("acme", "alice", role="admin")
    rb.grant_project("proj1", "alice", "user", role="admin")
    m = rb.fetch_membership("acme", "alice", "proj1")
    assert m.org_role == "admin"
    assert m.project_role == "admin"


def test_rbac_no_project_id(engine):
    rb = RbacStore(engine=engine)
    _seed_rbac(rb)
    m = rb.fetch_membership("acme", "alice")
    assert m.org_role == "member" and m.project_role is None
