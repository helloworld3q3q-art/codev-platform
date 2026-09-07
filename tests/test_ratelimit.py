"""SlidingWindowLimiter + default_key_from_scope 纯单测(不起服务)。"""
from __future__ import annotations

from dataclasses import dataclass

from codev_platform.core.ratelimit import (
    SlidingWindowLimiter,
    default_key_from_scope,
)


def test_within_window_allows_then_denies():
    lim = SlidingWindowLimiter(max_events=3, window_sec=60)
    assert lim.allow("u1", now=0) is True
    assert lim.allow("u1", now=1) is True
    assert lim.allow("u1", now=2) is True
    # 第 4 次仍在窗口内 → 拒
    assert lim.allow("u1", now=3) is False


def test_window_slides_and_reallows():
    lim = SlidingWindowLimiter(max_events=3, window_sec=60)
    for t in (0, 1, 2):
        assert lim.allow("u1", now=t) is True
    assert lim.allow("u1", now=3) is False
    # now=61: 早期 now=0 事件已过期(<= 61-60=1),剩 now=1,2 两个 → 放行
    assert lim.allow("u1", now=61) is True


def test_keys_isolated():
    lim = SlidingWindowLimiter(max_events=1, window_sec=60)
    assert lim.allow("a", now=0) is True
    assert lim.allow("a", now=0) is False  # a 满
    assert lim.allow("b", now=0) is True   # b 不受 a 影响


def test_max_le_zero_always_allows():
    lim = SlidingWindowLimiter(max_events=0, window_sec=60)
    for t in range(100):
        assert lim.allow("u1", now=t) is True
    neg = SlidingWindowLimiter(max_events=-5, window_sec=60)
    assert neg.allow("u1", now=0) is True


@dataclass
class _Ident:
    user_id: str


def test_key_from_scope_prefers_identity():
    scope = {"state": {"identity": _Ident("alice")}, "client": ("1.2.3.4", 5555)}
    assert default_key_from_scope(scope) == "alice"


def test_key_from_scope_falls_back_to_ip():
    scope = {"state": {}, "client": ("1.2.3.4", 5555)}
    assert default_key_from_scope(scope) == "1.2.3.4"
    # 无 state 也走 IP
    assert default_key_from_scope({"client": ("9.9.9.9", 1)}) == "9.9.9.9"


def test_key_from_scope_falls_back_to_anon():
    assert default_key_from_scope({}) == "anon"
    assert default_key_from_scope({"state": {"identity": None}}) == "anon"
