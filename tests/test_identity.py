"""core.identity 解析测试(memory 权限 M1)."""
from __future__ import annotations

import pytest

from codev_platform.core import identity


def test_validate_ok():
    assert identity.validate("alice") == "alice"
    assert identity.validate("user.1-2_3") == "user.1-2_3"


def test_validate_rejects_bad():
    for bad in ("", "a b", "a/b", "x" * 65, "用户"):
        with pytest.raises(ValueError):
            identity.validate(bad)


def test_resolve_local_default(monkeypatch):
    monkeypatch.delenv(identity.ENV_VAR, raising=False)
    assert identity.resolve_local() == "local"


def test_resolve_local_env(monkeypatch):
    monkeypatch.setenv(identity.ENV_VAR, "bob")
    assert identity.resolve_local() == "bob"


def test_resolve_from_request_header():
    assert identity.resolve_from_request({"X-User-Id": "carol"}) == "carol"
    assert identity.resolve_from_request({"x-user-id": "dave"}) == "dave"  # 大小写不敏感


def test_resolve_from_request_missing_falls_back(monkeypatch):
    monkeypatch.delenv(identity.ENV_VAR, raising=False)
    assert identity.resolve_from_request({}) == "local"  # 单人期不报错
