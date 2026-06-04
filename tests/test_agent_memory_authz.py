"""memory_authz 单测 —— topic_key 归一 + scope_decision 委托 + redline 写闸(P0 三缺口)。

纯逻辑 / 薄 store 读, 不碰真 PG: rbac store 用 fake 或 None。
"""
from __future__ import annotations

from types import SimpleNamespace

from codev_platform.agent import memory_authz
from codev_platform.core.rbac import Membership


# ---- make_topic_key(缺口 1: 三写入端统一 slug)----

def test_make_topic_key_none_and_empty():
    assert memory_authz.make_topic_key(None) is None
    assert memory_authz.make_topic_key("") is None
    assert memory_authz.make_topic_key("   ") is None
    assert memory_authz.make_topic_key("///") is None  # 全符号 → None


def test_make_topic_key_normalizes_variants_to_same_slug():
    # "Dark Mode" / "dark-mode" / "dark_mode" 必须归一到同一 key, 否则永不冲突消解。
    a = memory_authz.make_topic_key("Dark Mode")
    b = memory_authz.make_topic_key("dark-mode")
    c = memory_authz.make_topic_key("dark_mode")
    assert a == b == c == "dark-mode"


def test_make_topic_key_keeps_cjk_and_strips_edges():
    assert memory_authz.make_topic_key("  提交风格 Commit!! ") == "提交风格-commit"
    assert memory_authz.make_topic_key("--foo--") == "foo"


def test_make_topic_key_preserves_non_cjk_scripts():
    # 审计 NIT: isalnum 归一须保留日/韩/带音标拉丁, 不得吞成空 key 漏去重。
    assert memory_authz.make_topic_key("한국어") == "한국어"          # 纯韩文不丢
    assert memory_authz.make_topic_key("日本語 テスト") == "日本語-テスト"  # 假名保留
    assert memory_authz.make_topic_key("Café Résumé") == "café-résumé"   # 音标保留
    assert memory_authz.make_topic_key("emoji 🔥 test") == "emoji-test"  # 符号/emoji 当分隔


# ---- redline_write_allowed(缺口 3: 仅 org admin)----

class _Rbac:
    def __init__(self, m):
        self._m = m

    def fetch_membership(self, org_id, user_id, project_id=None):
        return self._m


def test_redline_denied_without_rbac_store(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    dec = memory_authz.redline_write_allowed("acme", "alice", SimpleNamespace())
    assert not dec.allowed


def test_redline_allowed_for_org_admin(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: _Rbac(Membership(org_role="admin")))
    dec = memory_authz.redline_write_allowed("acme", "alice", SimpleNamespace())
    assert dec.allowed


def test_redline_denied_for_org_member(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: _Rbac(Membership(org_role="member")))
    dec = memory_authz.redline_write_allowed("acme", "alice", SimpleNamespace())
    assert not dec.allowed


# ---- scope_decision(缺口 2: route + 工具共用同一闸)----

def test_scope_decision_personal_self_allowed(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {}}
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice")
    dec = memory_authz.scope_decision(cfg, "acme", "alice", "personal", "alice", ident)
    assert dec.allowed


def test_scope_decision_personal_other_denied(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {}}
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice")
    dec = memory_authz.scope_decision(cfg, "acme", "alice", "personal", "bob", ident)
    assert not dec.allowed


def test_scope_decision_project_uses_can_access(monkeypatch):
    import codev_platform.agent.deps as deps
    monkeypatch.setattr(deps, "get_rbac_store", lambda: None)
    cfg = {"gateway": {"auth_mode": "token"}, "projects": {}}
    ident = SimpleNamespace(via="token", org_id="acme", user_id="alice",
                            projects=frozenset({"p1"}), all_projects=False)
    assert memory_authz.scope_decision(cfg, "acme", "alice", "project", "p1", ident).allowed
    assert not memory_authz.scope_decision(cfg, "acme", "alice", "project", "p2", ident).allowed
