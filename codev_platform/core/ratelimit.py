"""纯滑动窗口限流器 —— 无 IO, now 由调用方传入(可测 / 确定性)。

设计:
- 与 acl / rbac 同风格:纯逻辑核心,状态在实例 dict,无中间件 / starlette 依赖。
- `allow(key, now)` 确定性:给定相同 (key, now) 序列,结果可复现 → 单测直接喂时间戳断言。
- IO / ASGI 包装在 gateway/middleware.py 的 RateLimitMiddleware,本模块不碰。

滑窗语义:每 key 维护事件时间戳列表;调用 allow 时剔除 <= now - window_sec 的旧事件,
若剩余 < max_events 则记录 now 并放行,否则拒绝(不记录)。
"""
from __future__ import annotations

from collections.abc import Mapping


class SlidingWindowLimiter:
    """每 key 一个滑动窗口。纯内存,无锁(单进程 daemon 内 ASGI 串行调用足够)。

    max_events <= 0 视为不限流(恒放行,且不积累状态)。
    """

    def __init__(self, max_events: int, window_sec: float) -> None:
        self._max = int(max_events)
        self._window = float(window_sec)
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str, now: float) -> bool:
        # max<=0 视为不限:恒放行,不积累状态(防内存膨胀)。
        if self._max <= 0:
            return True
        cutoff = now - self._window
        lst = self._events.get(key)
        if lst is None:
            lst = []
            self._events[key] = lst
        # 原地剔除过期(<= cutoff)时间戳;保留窗口内的。
        kept = [t for t in lst if t > cutoff]
        lst[:] = kept
        if len(lst) < self._max:
            lst.append(now)
            return True
        return False


def default_key_from_scope(scope: Mapping) -> str:
    """从 ASGI scope 取限流 key(纯,不 import starlette,直接读 dict)。

    优先 state.identity.user_id(认证后)→ 退 client IP → 再退 "anon"。
    """
    state = scope.get("state")
    if isinstance(state, Mapping):
        identity = state.get("identity")
        uid = getattr(identity, "user_id", None)
        if uid:
            return str(uid)
    client = scope.get("client")
    if client:
        # ASGI client = (host, port) 元组
        try:
            return str(client[0])
        except (TypeError, IndexError, KeyError):
            pass
    return "anon"
