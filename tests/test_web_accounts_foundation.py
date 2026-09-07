"""Auth/Orgs/Users 地基测试 —— 密码 / 会话 / platform_admin bypass / org 角色依赖。"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi import APIRouter, Depends  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import codev_platform.core.httpkit.permissions as perm  # noqa: E402
import codev_platform.web.security.deps as wdeps  # noqa: E402
from codev_platform.core.httpkit import build_app, ok  # noqa: E402
from codev_platform.core.httpkit.permissions import require_project_access  # noqa: E402
from codev_platform.gateway.auth import token_hash  # noqa: E402
from codev_platform.web.domain.accounts import OrgMember  # noqa: E402
from codev_platform.web.repositories.account_store import member_store  # noqa: E402
from codev_platform.web.security.deps import current_session, require_org_role  # noqa: E402
from codev_platform.web.security.passwords import hash_password, verify_password  # noqa: E402
from codev_platform.web.security.sessions import SessionStore, session_store  # noqa: E402


# ---- 密码 ----

def test_password_hash_verify():
    h = hash_password("s3cret")
    assert h != "s3cret" and h.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("x", "not-a-hash") is False


# ---- 会话 ----

def test_session_create_resolve_expire():
    s = SessionStore(access_ttl=100)
    t = s.create("u1", "orgA", now=1000.0)
    assert s.resolve(t.access_token, now=1050.0).username == "u1"
    assert s.resolve(t.access_token, now=2000.0) is None   # 过期
    assert s.resolve("bogus", now=1050.0) is None


def test_session_refresh_rotates_and_revoke():
    s = SessionStore()
    t = s.create("u1", "orgA", now=1000.0)
    t2 = s.refresh(t.refresh_token, now=1100.0)
    assert t2 is not None and t2.access_token != t.access_token
    assert s.refresh(t.refresh_token, now=1200.0) is None   # 旧 refresh 已轮换失效
    s.revoke(t2.refresh_token)
    assert s.refresh(t2.refresh_token, now=1300.0) is None


# ---- platform_admin 白名单 + bypass ----

def test_platform_admin_whitelist(monkeypatch):
    from codev_platform.core.platform_admin import is_platform_admin
    cfg = {"platform_admins": ["super"]}
    assert is_platform_admin(cfg, "super") is True
    assert is_platform_admin(cfg, "normal") is False
    assert is_platform_admin(cfg, None) is False


def _bypass_app(cfg):
    r = APIRouter()

    @r.post("/api/v1/x")
    def x(ctx=Depends(require_project_access)):  # noqa: ANN001
        return ok({"ok": True})

    return build_app(title="t", routers=[r], cfg=cfg, public_paths=("/health",))


def test_require_project_access_platform_admin_bypass(monkeypatch):
    tok = "T-super"
    cfg = {
        "gateway": {"auth_mode": "token", "tokens": {token_hash(tok): {"user_id": "super", "org_id": "o", "projects": []}}},
        "platform_admins": ["super"], "projects": {},
    }
    monkeypatch.setattr(perm, "load_config", lambda: cfg)
    c = TestClient(_bypass_app(cfg))
    # super 是 platform_admin → 即使 token 模式无 project_id 也放行
    r = c.post("/api/v1/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["result"] == 0


def test_require_project_access_non_admin_denied(monkeypatch):
    tok = "T-norm"
    cfg = {
        "gateway": {"auth_mode": "token", "tokens": {token_hash(tok): {"user_id": "norm", "org_id": "o", "projects": []}}},
        "platform_admins": ["super"], "projects": {},
    }
    monkeypatch.setattr(perm, "load_config", lambda: cfg)
    c = TestClient(_bypass_app(cfg))
    r = c.post("/api/v1/x", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403 and r.json()["result"] == 1


# ---- current_session + require_org_role (passthrough 模式 + 会话) ----

def _role_app():
    r = APIRouter()
    require_admin = require_org_role("admin")

    @r.get("/api/v1/me")
    def me(sess=Depends(current_session)):  # noqa: ANN001
        return ok({"username": sess.username})

    @r.post("/api/v1/admin-only")
    def admin_only(sess=Depends(require_admin)):  # noqa: ANN001
        return ok({"ok": True})

    cfg = {"gateway": {"auth_mode": "passthrough"}, "platform_admins": [], "projects": {}}
    return build_app(title="t", routers=[r], cfg=cfg, public_paths=("/health",))


def test_current_session_and_org_role(monkeypatch):
    monkeypatch.setattr(wdeps, "load_config", lambda: {"platform_admins": []})
    c = TestClient(_role_app())
    # 未登录 → 403
    assert c.get("/api/v1/me").status_code == 403
    # 登录态 (会话 token)
    t = session_store.create("alice", "orgA")
    auth = {"Authorization": f"Bearer {t.access_token}"}
    assert c.get("/api/v1/me", headers=auth).json()["data"]["username"] == "alice"
    # alice 非 admin → admin-only 403
    assert c.post("/api/v1/admin-only", headers=auth).status_code == 403
    # 给 alice org admin 角色 → 放行
    member_store.upsert(OrgMember(org_id="orgA", username="alice", role="admin"))
    assert c.post("/api/v1/admin-only", headers=auth).status_code == 200
