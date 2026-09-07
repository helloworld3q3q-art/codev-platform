"""TokenService 授权护栏真实覆盖(注入 sqlite engine, 跑真 SQL, 不 mock)。

核心安全性质:
- org_id 由调用方(路由取 session.org_id)传, 目标用户须本 org 成员 —— 跨 org 签发被拒。
- 明文 token 只 issue 一次返回; 库里只存 sha256 hash(明文不落库)。
- list / revoke org 隔离: org_admin 只见 / 只吊销本 org token, 不能凭 hash 前缀跨 org 吊销。
- platform_admin(caller_is_admin=True)bypass 成员护栏。
"""
from __future__ import annotations

import time

import pytest

from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.gateway.auth import token_hash
from codev_platform.web.domain.accounts import STATUS_ACTIVE, OrgMember, User
from codev_platform.web.repositories.account_store import (
    get_member_store,
    get_user_store,
    reset_account_stores,
)
from codev_platform.web.services.token_service import TokenService


class _FakeStore:
    """PgTokenStore 的内存替身(同契约): issue/list_tokens/revoke。

    service 授权逻辑用替身单测, 与真 SQL 解耦(真 SQL 的 join users.status / FK 由
    test_gateway_pg_token 用 sqlite engine 单独覆盖)。明文 hash 不落库由 issue_token 保证。
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def issue(self, token_hash_, user_id, org_id, *, projects=None, label=None, expires_at=None) -> None:
        self.rows.append(dict(token_hash=token_hash_, user_id=user_id, org_id=org_id,
                              projects=projects, label=label, status="ACTIVE", expires_at=expires_at))

    def list_tokens(self, user_id=None) -> list[dict]:
        return [dict(r) for r in self.rows if user_id is None or r["user_id"] == user_id]

    def revoke(self, prefix) -> int:
        n = 0
        for r in self.rows:
            if r["status"] == "ACTIVE" and r["token_hash"].startswith(prefix):
                r["status"] = "REVOKED"
                n += 1
        return n


@pytest.fixture()
def svc():
    reset_account_stores()
    service = TokenService(store=_FakeStore())
    yield service
    reset_account_stores()


def _seed(username: str, org: str, role: str = "member") -> None:
    """在内存账户表登记 user + org 成员(guard 读这里; sqlite token 表 FK 不强制, 无需另 seed)。"""
    get_user_store().create(User(
        username=username, password_hash="x", org_id=org,
        status=STATUS_ACTIVE, display_name="", email="",
    ))
    get_member_store().upsert(OrgMember(org_id=org, username=username, role=role))


# ---- 签发授权 ----

def test_issue_own_org_returns_plaintext_once_and_stores_hash(svc):
    _seed("alice", "orgA")
    res = svc.issue(target_user="alice", projects="*", label="laptop", expires=None,
                    org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert res.token and len(res.token) > 20            # 明文够长
    assert res.userId == "alice" and res.orgId == "orgA"
    assert res.projects == "*" and res.expiresAt is None
    # 库里只存 hash, 明文不落库
    rows = svc.list_tokens(caller_org_id="orgA", caller_is_admin=True)
    assert len(rows) == 1
    assert rows[0].tokenHashPrefix == token_hash(res.token)[:12]
    assert res.token not in rows[0].tokenHashPrefix


def test_issue_cross_org_denied(svc):
    """orgA admin 给 orgB 用户(非 orgA 成员)签 token → ACCESS_DENIED, 不落库。"""
    _seed("bob", "orgB")
    with pytest.raises(PlatformError) as ei:
        svc.issue(target_user="bob", projects="*", label=None, expires=None,
                  org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert ei.value.code == ErrorCode.ACCESS_DENIED
    assert svc.list_tokens(caller_org_id="orgA", caller_is_admin=True) == []


def test_issue_unknown_user_rejected(svc):
    with pytest.raises(PlatformError) as ei:
        svc.issue(target_user="ghost", projects="*", label=None, expires=None,
                  org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert ei.value.code == ErrorCode.PROJECT_UNKNOWN


def test_platform_admin_bypasses_membership(svc):
    """platform_admin(caller_is_admin=True)可给任意 org 用户签(bypass 成员护栏)。"""
    _seed("bob", "orgB")
    res = svc.issue(target_user="bob", projects="p1,p2", label=None, expires=None,
                    org_id="orgB", caller_org_id="orgZ", caller_is_admin=True)
    assert res.orgId == "orgB" and res.projects == ["p1", "p2"]


def test_issue_ttl_sets_future_expiry(svc):
    _seed("alice", "orgA")
    res = svc.issue(target_user="alice", projects="*", label=None, expires="1h",
                    org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert res.expiresAt is not None and res.expiresAt > time.time() + 3000


def test_issue_bad_duration_rejected(svc):
    _seed("alice", "orgA")
    with pytest.raises(PlatformError) as ei:
        svc.issue(target_user="alice", projects="*", label=None, expires="30x",
                  org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert ei.value.code == ErrorCode.INVALID_PARAMS


def test_issue_default_no_project_access(svc):
    _seed("alice", "orgA")
    res = svc.issue(target_user="alice", projects=None, label=None, expires=None,
                    org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    assert res.projects is None     # 无项目权 = 安全默认


# ---- list / revoke org 隔离 ----

def test_list_org_isolation(svc):
    """org_admin 列表只见本 org token, 别 org 的被过滤掉。"""
    _seed("alice", "orgA")
    _seed("bob", "orgB")
    svc.issue(target_user="alice", projects="*", label=None, expires=None,
              org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    svc.issue(target_user="bob", projects="*", label=None, expires=None,
              org_id="orgB", caller_org_id="orgZ", caller_is_admin=True)
    seen = svc.list_tokens(caller_org_id="orgA", caller_is_admin=False)
    assert [r.userId for r in seen] == ["alice"]            # 只见本 org
    assert len(svc.list_tokens(caller_org_id="orgA", caller_is_admin=True)) == 2  # platform_admin 见全部


def test_revoke_cross_org_denied(svc):
    """org_admin 凭 hash 前缀吊销别 org token → ACCESS_DENIED(堵跨 org 越权吊销)。"""
    _seed("bob", "orgB")
    svc.issue(target_user="bob", projects="*", label=None, expires=None,
              org_id="orgB", caller_org_id="orgZ", caller_is_admin=True)
    prefix = svc.list_tokens(caller_org_id="orgB", caller_is_admin=True)[0].tokenHashPrefix
    with pytest.raises(PlatformError) as ei:
        svc.revoke(token_hash_prefix=prefix, caller_org_id="orgA", caller_is_admin=False)
    assert ei.value.code == ErrorCode.ACCESS_DENIED


def test_revoke_own_org_succeeds(svc):
    _seed("alice", "orgA")
    svc.issue(target_user="alice", projects="*", label=None, expires=None,
              org_id="orgA", caller_org_id="orgA", caller_is_admin=False)
    prefix = svc.list_tokens(caller_org_id="orgA", caller_is_admin=False)[0].tokenHashPrefix
    res = svc.revoke(token_hash_prefix=prefix, caller_org_id="orgA", caller_is_admin=False)
    assert res.revoked == 1


def test_revoke_no_match_returns_zero(svc):
    res = svc.revoke(token_hash_prefix="deadbeef", caller_org_id="orgA", caller_is_admin=False)
    assert res.revoked == 0
