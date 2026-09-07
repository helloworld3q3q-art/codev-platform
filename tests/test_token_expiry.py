"""#8(部分): token 过期 + 轮换 —— 纯函数 + TokenAuthenticator 过期拒绝(不起服务)。"""
from __future__ import annotations

import time

import pytest

from codev_platform.gateway.auth import (
    TokenAuthenticator,
    Unauthorized,
    token_expired,
    token_hash,
)
from codev_platform.ops.gateway import parse_duration


# ---- token_expired(纯函数, now 入参)----

def test_token_expired_no_field_never_expires():
    assert token_expired({}, 1000.0) is False
    assert token_expired({"expires_at": None}, 1000.0) is False
    assert token_expired({"expires_at": ""}, 1000.0) is False


def test_token_expired_future_not_expired():
    assert token_expired({"expires_at": 2000.0}, 1000.0) is False


def test_token_expired_past_expired():
    assert token_expired({"expires_at": 500.0}, 1000.0) is True


def test_token_expired_iso_string():
    assert token_expired({"expires_at": "2020-01-01T00:00:00+00:00"}, time.time()) is True
    assert token_expired({"expires_at": "2999-01-01T00:00:00Z"}, time.time()) is False


def test_token_expired_bad_value_treated_expired():
    # 坏值按已过期(安全默认拒)
    assert token_expired({"expires_at": "not-a-date"}, 1000.0) is True
    assert token_expired({"expires_at": True}, 1000.0) is True  # bool 不是合法 epoch


# ---- parse_duration(纯函数)----

def test_parse_duration_units():
    assert parse_duration("30d") == 30 * 86400
    assert parse_duration("12h") == 12 * 3600
    assert parse_duration("90m") == 90 * 60
    assert parse_duration("45s") == 45


def test_parse_duration_empty_is_permanent():
    assert parse_duration("") is None
    assert parse_duration(None) is None
    assert parse_duration("  ") is None


def test_parse_duration_illegal_raises():
    for bad in ("30", "d", "0d", "-5h", "12x", "abc"):
        with pytest.raises(ValueError):
            parse_duration(bad)


# ---- TokenAuthenticator 过期 token 被拒(构造 meta)----

def test_authenticator_rejects_expired_token():
    past = time.time() - 100
    a = TokenAuthenticator({token_hash("t"): {"user_id": "u", "expires_at": past}})
    with pytest.raises(Unauthorized):
        a.authenticate({"Authorization": "Bearer t"})


def test_authenticator_accepts_unexpired_token():
    future = time.time() + 3600
    a = TokenAuthenticator({token_hash("t"): {"user_id": "u", "expires_at": future}})
    idt = a.authenticate({"Authorization": "Bearer t"})
    assert idt.user_id == "u" and idt.via == "token"


def test_authenticator_no_expiry_still_valid():
    a = TokenAuthenticator({token_hash("t"): {"user_id": "u"}})
    assert a.authenticate({"Authorization": "Bearer t"}).user_id == "u"
