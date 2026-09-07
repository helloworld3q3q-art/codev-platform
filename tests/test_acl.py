"""项目级 ACL 纯函数单测 (codev_platform.core.acl.can_access)。

用轻量 fake identity (SimpleNamespace 带 via/org_id/projects/all_projects),
不依赖 gateway.auth.Identity 构造, 验两道闸 + passthrough advisory 放行语义。
"""
from __future__ import annotations

from types import SimpleNamespace

from codev_platform.core.acl import can_access, memory_scope_access


def _ident(*, via="token", org_id="orgA", projects=frozenset(), all_projects=False, user_id="userA"):
    return SimpleNamespace(via=via, org_id=org_id, projects=projects,
                           all_projects=all_projects, user_id=user_id)


def test_passthrough_mode_advisory_allow_any_project():
    # cfg gateway.auth_mode 缺省 = passthrough: 任意 project 放行且 advisory=True
    d = can_access({}, None, "openclaw-stock")
    assert d.allowed is True
    assert d.advisory is True


def test_passthrough_explicit_advisory():
    cfg = {"gateway": {"auth_mode": "passthrough"}}
    d = can_access(cfg, _ident(), "anything")
    assert d.allowed is True
    assert d.advisory is True


def _token_cfg(projects: dict | None = None) -> dict:
    return {"gateway": {"auth_mode": "token"}, "projects": projects or {}}


def test_token_mode_requires_authenticated_identity():
    # identity=None
    d = can_access(_token_cfg(), None, "openclaw-stock")
    assert d.allowed is False
    # via != token
    d2 = can_access(_token_cfg(), _ident(via="passthrough", all_projects=True), "openclaw-stock")
    assert d2.allowed is False


def test_token_gate1_org_mismatch_denied():
    cfg = _token_cfg({"openclaw-stock": {"org_id": "orgB"}})
    d = can_access(cfg, _ident(org_id="orgA", all_projects=True), "openclaw-stock")
    assert d.allowed is False
    assert "org mismatch" in d.reason


def test_token_project_org_absent_is_public():
    # 项目未登记 org_id -> 公开, 不卡 org 闸; 白名单命中即放行
    cfg = _token_cfg({"openclaw-stock": {}})
    d = can_access(cfg, _ident(projects=frozenset({"openclaw-stock"})), "openclaw-stock")
    assert d.allowed is True


def test_token_all_projects_allowed():
    cfg = _token_cfg({"openclaw-stock": {"org_id": "orgA"}})
    d = can_access(cfg, _ident(org_id="orgA", all_projects=True), "openclaw-stock")
    assert d.allowed is True
    assert d.advisory is False


def test_token_project_in_allowlist_allowed():
    cfg = _token_cfg()
    d = can_access(cfg, _ident(projects=frozenset({"openclaw-stock", "other"})), "openclaw-stock")
    assert d.allowed is True


def test_token_project_not_in_allowlist_denied():
    cfg = _token_cfg()
    d = can_access(cfg, _ident(projects=frozenset({"other"})), "openclaw-stock")
    assert d.allowed is False
    assert "not in token allowlist" in d.reason


def test_token_no_project_scope_denied():
    # token 模式无显式 project_id -> deny(防越权扫盲, 不再 cwd 回退放行)
    cfg = _token_cfg()
    d = can_access(cfg, _ident(), None)
    assert d.allowed is False
    assert "explicit project_id" in d.reason


def test_passthrough_no_project_scope_allowed():
    # passthrough 模式 project_id=None 仍放行 advisory
    d = can_access({}, None, None)
    assert d.allowed is True
    assert d.advisory is True


def test_internal_identity_web_vouched_allowed():
    # via=internal = web 前门已 require_project_access 鉴权 + HMAC 验签的服务间信物;
    # token 模式下也直接放行(不看白名单/org), 且非 advisory(审计必记)。
    d = can_access(_token_cfg(), _ident(via="internal", projects=frozenset(), all_projects=False),
                   "openclaw-stock")
    assert d.allowed is True
    assert d.advisory is False


def test_internal_identity_allowed_even_on_org_mismatch():
    # internal 信物跨 org 也放行(web 前门已判, 含 platform_admin bypass 场景)。
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {"openclaw-stock": {"org_id": "orgX"}}}
    d = can_access(cfg, _ident(via="internal", org_id="orgA"), "openclaw-stock")
    assert d.allowed is True


# ---- memory_scope_access ----

def test_memory_personal_self_allowed():
    d = memory_scope_access(_token_cfg(), _ident(user_id="userA"), "personal", "userA")
    assert d.allowed is True


def test_memory_personal_other_denied():
    d = memory_scope_access(_token_cfg(), _ident(user_id="userA"), "personal", "userB")
    assert d.allowed is False


def test_memory_project_delegates_to_can_access():
    cfg = _token_cfg()
    allow = memory_scope_access(cfg, _ident(projects=frozenset({"openclaw-stock"})),
                                "project", "openclaw-stock")
    assert allow.allowed is True
    deny = memory_scope_access(cfg, _ident(projects=frozenset({"other"})),
                               "project", "openclaw-stock")
    assert deny.allowed is False


def test_memory_org_team_passthrough_allowed():
    for scope in ("org", "team"):
        d = memory_scope_access({}, _ident(), scope, "orgA")
        assert d.allowed is True
        assert d.advisory is True


def test_memory_org_team_token_denied():
    for scope in ("org", "team"):
        d = memory_scope_access(_token_cfg(), _ident(), scope, "orgA")
        assert d.allowed is False


def test_memory_unknown_scope_denied():
    d = memory_scope_access(_token_cfg(), _ident(), "galaxy", "x")
    assert d.allowed is False
    assert "unknown memory scope" in d.reason
