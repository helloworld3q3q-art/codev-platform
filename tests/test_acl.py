"""项目级 ACL 纯函数单测 (codev_platform.core.acl.can_access)。

用轻量 fake identity (SimpleNamespace 带 via/org_id/projects/all_projects),
不依赖 gateway.auth.Identity 构造, 验两道闸 + passthrough advisory 放行语义。
"""
from __future__ import annotations

from types import SimpleNamespace

from codev_platform.core.acl import can_access


def _ident(*, via="token", org_id="orgA", projects=frozenset(), all_projects=False):
    return SimpleNamespace(via=via, org_id=org_id, projects=projects, all_projects=all_projects)


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


def test_token_no_project_scope_cwd_fallback_allowed():
    cfg = _token_cfg()
    d = can_access(cfg, _ident(), None)
    assert d.allowed is True
    assert "cwd fallback" in d.reason
