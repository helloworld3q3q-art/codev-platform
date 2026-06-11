"""ADVERSARIAL audit — temporary, delete after run."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.web.routes.users as users_routes  # noqa: E402
import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.domain.accounts import OrgMember, User  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    member_store,
    reset_account_stores,
    user_store,
)
from codev_platform.web.routes import users  # noqa: E402
from codev_platform.web.security.passwords import hash_password  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


@pytest.fixture(autouse=True)
def _iso(monkeypatch):
    reset_account_stores()
    session_store.clear()
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": []})
    monkeypatch.setattr(users_routes, "load_config", lambda: {"platform_admins": []})
    yield
    reset_account_stores()
    session_store.clear()


@pytest.fixture
def client():
    return TestClient(build_app(title="t", routers=[users.router], cfg=_CFG))


def _org_admin(org, username):
    user_store.upsert(User(username=username, password_hash=hash_password("pw123456"), org_id=org))
    member_store.upsert(OrgMember(org_id=org, username=username, role="admin"))
    t = session_store.create(username, org)
    return {"Authorization": f"Bearer {t.access_token}"}


# ATTACK 1: org_admin of orgA tries to grant role to a user whose home org is orgB,
# but passes org_id=orgA (pull external user into own org).
def test_attack_org_admin_pulls_external_user_into_own_org(client):
    _org_admin("orgA", "bossA")
    auth = _org_admin("orgA", "bossA")
    # victim's home org is orgB
    user_store.upsert(User(username="victim", password_hash=hash_password("x12345678"), org_id="orgB"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "victim", "orgId": "orgA", "role": "admin"})
    print("ATTACK1 status", r.status_code, r.json())
    assert r.status_code != 200, "org_admin pulled foreign-home user into own org!"
    assert member_store.get("orgA", "victim") is None


# ATTACK 2: org_admin of orgA grants role in orgB (write to foreign org) for own-org user.
def test_attack_org_admin_writes_foreign_org(client):
    auth = _org_admin("orgA", "bossA")
    user_store.upsert(User(username="u", password_hash=hash_password("x12345678"), org_id="orgA"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "u", "orgId": "orgB", "role": "admin"})
    print("ATTACK2 status", r.status_code, r.json())
    assert r.status_code != 200
    assert member_store.get("orgB", "u") is None


# ATTACK 3: escalation chain — can an org_admin self-grant by passing own username + own org?
# (Legit; should succeed but only within own org — verify it can't touch other org.)
def test_attack_self_grant_stays_in_own_org(client):
    auth = _org_admin("orgA", "bossA")
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "bossA", "orgId": "orgA", "role": "admin"})
    assert r.status_code == 200
    assert member_store.get("orgB", "bossA") is None


# ATTACK 4: planted ghost membership escalation.
# platform_admin plants OrgMember(orgB, victim, admin) though victim.org_id == orgA.
# Does require_org_role read it as escalation when victim logs in to orgB?
# victim's session.org_id is fixed at login to victim.org_id (orgA). Can victim get a session
# bound to orgB? Login uses user.org_id only -> session.org_id == orgA. So ghost in orgB
# should NOT grant victim admin via normal login.
def test_attack_ghost_membership_not_reachable_via_login(client, monkeypatch):
    # platform_admin path: boss is platform_admin, plants cross-org membership
    monkeypatch.setattr(users_routes, "load_config", lambda: {"platform_admins": ["boss"]})
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": ["boss"]})
    user_store.upsert(User(username="boss", password_hash=hash_password("x12345678"), org_id="orgA"))
    t = session_store.create("boss", "orgA")
    admin_auth = {"Authorization": f"Bearer {t.access_token}"}
    # victim home = orgA, plant admin in orgB
    user_store.upsert(User(username="victim", password_hash=hash_password("x12345678"), org_id="orgA"))
    r = client.post("/api/v1/users/roles", headers=admin_auth,
                    json={"username": "victim", "orgId": "orgB", "role": "admin"})
    assert r.status_code == 200
    # victim logs in -> session.org_id = victim.org_id = orgA (login binds home org)
    vt = session_store.create("victim", "orgA")
    vauth = {"Authorization": f"Bearer {vt.access_token}"}
    # victim has NO membership in orgA -> should be denied admin actions
    monkeypatch.setattr(users_routes, "load_config", lambda: {"platform_admins": []})
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": []})
    r2 = client.post("/api/v1/users/list", headers=vauth, json={})
    print("ATTACK4 victim list status", r2.status_code, r2.json())
    # victim's orgB admin ghost must NOT grant orgA admin
    assert r2.status_code != 200 or r2.json().get("result") != 0


# ATTACK 5: empty/whitespace org_id bypass — does strip() + comparison let "" sneak in?
def test_attack_empty_org_id(client):
    auth = _org_admin("orgA", "bossA")
    user_store.upsert(User(username="u", password_hash=hash_password("x12345678"), org_id="orgA"))
    r = client.post("/api/v1/users/roles", headers=auth,
                    json={"username": "u", "orgId": "  ", "role": "admin"})
    print("ATTACK5 status", r.status_code, r.json())
    # org_admin: stripped "" != caller_org_id "orgA" -> must reject
    assert r.status_code != 200
