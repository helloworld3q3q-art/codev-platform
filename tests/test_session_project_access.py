"""require_project_access 的 via=session 分支 + session_project_decision adapter(codex P2 #7)。

prod token 模式下 web 登录(via=session)访问项目路由曾被 acl.can_access 全 deny。修复: 依赖倒置
注入钩子, web 启动注入 session_project_decision(复刻 _authorize_project 三步); MCP/agent 进程
不注入 → session 恒 deny。本测覆盖: 分发逻辑(permissions 层) + 防越权(adapter 层 org 隔离/role)。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from codev_platform.core import acl
from codev_platform.core.errors import PlatformError
from codev_platform.core.httpkit import permissions
from codev_platform.web.services import project_service as ps


def _req(identity, project_id=None):
    headers = {"X-Project-Id": project_id} if project_id else {}
    return SimpleNamespace(state=SimpleNamespace(identity=identity), headers=headers)


def _sess_idn(user="alice", org="acme"):
    return SimpleNamespace(user_id=user, org_id=org, via="session")


# ---- permissions 层: 分发逻辑(不碰 RBAC, 用 fake checker) ----

@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    permissions._reset_session_access_checker()
    monkeypatch.setattr(permissions, "load_config",
                        lambda: {"gateway": {"auth_mode": "token"}, "projects": {}})
    monkeypatch.setattr(permissions, "is_platform_admin", lambda cfg, u: False)
    yield
    permissions._reset_session_access_checker()


def test_session_allowed_when_checker_grants():
    permissions.set_session_access_checker(lambda idn, pid: acl.AccessDecision(True, "ok"))
    idn, pid = permissions.require_project_access(_req(_sess_idn(), "proj1"))
    assert pid == "proj1"


def test_session_denied_when_checker_rejects():
    permissions.set_session_access_checker(lambda idn, pid: acl.AccessDecision(False, "no"))
    with pytest.raises(PlatformError):
        permissions.require_project_access(_req(_sess_idn(), "proj1"))


def test_session_denied_when_no_checker_registered():
    # 模拟 MCP/agent 进程: 未注入 checker → session 身份恒 deny(约束②, 物理隔离)。
    with pytest.raises(PlatformError):
        permissions.require_project_access(_req(_sess_idn(), "proj1"))


def test_token_identity_still_uses_can_access(monkeypatch):
    # via=token 不走 session 分支(回归: 原 acl.can_access 路径不变)。
    called = {}

    def _fake_can_access(cfg, idn, pid):
        called["hit"] = True
        return acl.AccessDecision(True, "tok")

    monkeypatch.setattr(permissions.acl, "can_access", _fake_can_access)
    idn = SimpleNamespace(user_id="u", org_id="o", via="token")
    permissions.require_project_access(_req(idn, "proj1"))
    assert called.get("hit")  # token 身份确实走了 can_access, 没被 session 分支截走


# ---- adapter 层: 防越权(session_project_decision) ----

def _adapter_env(monkeypatch, *, project_org, has_role=True, admin=False):
    monkeypatch.setattr(ps, "load_config", lambda: {})
    monkeypatch.setattr(ps, "is_platform_admin", lambda cfg, u: admin)
    repo = SimpleNamespace(get_project_detail=lambda pid: (
        {"code": pid, "orgId": project_org} if project_org is not None else None))
    monkeypatch.setattr(ps, "ProjectReadRepository", lambda: repo)
    monkeypatch.setattr(ps, "can_access_project", lambda sess, pid, action: has_role)


def test_adapter_no_project_id_deny():
    assert not ps.session_project_decision(_sess_idn(), None).allowed


def test_adapter_unregistered_project_deny(monkeypatch):
    _adapter_env(monkeypatch, project_org=None)  # repo 返 None
    assert not ps.session_project_decision(_sess_idn(), "ghost").allowed


def test_adapter_cross_org_deny(monkeypatch):
    # 🔴 盲区3 回归: project 属 org-B, 用户 org-A → org 隔离 deny(即使有 role)。
    _adapter_env(monkeypatch, project_org="org-B", has_role=True)
    d = ps.session_project_decision(SimpleNamespace(user_id="a", org_id="org-A", via="session"), "p")
    assert not d.allowed and "org" in d.reason


def test_adapter_in_org_with_role_allow(monkeypatch):
    _adapter_env(monkeypatch, project_org="org-A", has_role=True)
    d = ps.session_project_decision(SimpleNamespace(user_id="a", org_id="org-A", via="session"), "p")
    assert d.allowed


def test_adapter_in_org_no_role_deny(monkeypatch):
    _adapter_env(monkeypatch, project_org="org-A", has_role=False)
    d = ps.session_project_decision(SimpleNamespace(user_id="b", org_id="org-A", via="session"), "p")
    assert not d.allowed


def test_adapter_platform_admin_cross_org_allow(monkeypatch):
    # platform_admin 跨 org 放行(不查 project org)。
    _adapter_env(monkeypatch, project_org="org-Z", has_role=False, admin=True)
    assert ps.session_project_decision(_sess_idn(), "p").allowed
