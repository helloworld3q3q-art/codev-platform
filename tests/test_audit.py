"""结构化访问审计日志单测 (codev_platform.core.audit.audit_access)。

用 tmp 路径 monkeypatch audit_log_path 避免污染真日志。
轻量 fake identity/decision (SimpleNamespace), 不依赖真 Identity/AccessDecision 构造。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codev_platform.core import audit


def _ident(user_id="alice", org_id="acme", via="token"):
    return SimpleNamespace(user_id=user_id, org_id=org_id, via=via)


def _dec(allowed, reason="r", advisory=False):
    return SimpleNamespace(allowed=allowed, reason=reason, advisory=advisory)


@pytest.fixture()
def log_file(tmp_path, monkeypatch):
    p = tmp_path / "access.jsonl"
    monkeypatch.setattr(audit, "audit_log_path", lambda: p)
    return p


def _lines(p):
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_advisory_allow_skipped(log_file):
    # passthrough advisory allow 不写 (dev 放行噪音)
    audit.audit_access("chroma", _ident(via="passthrough"), "openclaw-stock",
                       _dec(True, "passthrough(dev): advisory allow", advisory=True))
    assert _lines(log_file) == []


def test_deny_written_with_fields(log_file):
    audit.audit_access("cross-link", _ident(user_id="bob"), "secret-proj",
                       _dec(False, "project secret-proj not in token allowlist"))
    recs = _lines(log_file)
    assert len(recs) == 1
    r = recs[0]
    assert r["service"] == "cross-link"
    assert r["user_id"] == "bob"
    assert r["project_id"] == "secret-proj"
    assert r["allowed"] is False
    assert "not in token allowlist" in r["reason"]
    assert "ts" in r and r["via"] == "token" and r["org_id"] == "acme"


def test_token_allow_written(log_file):
    # token 真授权 allow (非 advisory) 要留痕
    audit.audit_access("codegraph", _ident(), "openclaw-stock",
                       _dec(True, "token: project in allowlist", advisory=False))
    recs = _lines(log_file)
    assert len(recs) == 1
    assert recs[0]["allowed"] is True
    assert recs[0]["project_id"] == "openclaw-stock"


def test_write_failure_is_silent(monkeypatch):
    # 审计写失败不得影响主流程 (不抛异常)
    def _boom():
        raise OSError("disk full")
    monkeypatch.setattr(audit, "audit_log_path", _boom)
    audit.audit_access("chroma", _ident(), "p", _dec(False, "denied"))  # 不应抛


def test_audit_log_path_under_data_root(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "data_root", lambda: tmp_path)
    p = audit.audit_log_path()
    assert p == tmp_path / "audit" / "access.jsonl"
    assert (tmp_path / "audit").is_dir()
