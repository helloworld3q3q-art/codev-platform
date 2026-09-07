"""logout 撤销整个会话 —— revoke(refresh) 同时使 access + refresh 失效。

回归: 修复前 revoke 只 pop refresh_hash, access token 仍能 resolve (登出后残留可用)。
直接测 SessionStore (不经 HTTP), 用 now 注入避免时钟依赖。
"""
from __future__ import annotations

from codev_platform.web.security.sessions import SessionStore


def test_revoke_invalidates_access_and_refresh():
    store = SessionStore()
    issued = store.create("alice", "orgA", now=1000.0)
    access, refresh = issued.access_token, issued.refresh_token

    # 登录后 access 可用
    assert store.resolve(access, now=1000.0) is not None

    # logout
    store.revoke(refresh)

    # access 与 refresh 一并失效
    assert store.resolve(access, now=1000.0) is None
    assert store.refresh(refresh, now=1000.0) is None


def test_revoke_unknown_token_is_silent_idempotent():
    store = SessionStore()
    issued = store.create("bob", "orgB", now=1000.0)

    # 撤销未知 token 不抛, 不影响既有会话
    store.revoke("never-issued")
    store.revoke("")
    assert store.resolve(issued.access_token, now=1000.0) is not None

    # 重复撤销同一 refresh 幂等
    store.revoke(issued.refresh_token)
    store.revoke(issued.refresh_token)
    assert store.resolve(issued.access_token, now=1000.0) is None


def test_refresh_rotation_still_works_after_session_tracking():
    store = SessionStore()
    issued = store.create("carol", "orgC", now=1000.0)
    rotated = store.refresh(issued.refresh_token, now=1000.0)
    assert rotated is not None
    # 旧 access/refresh 失效, 新的可用
    assert store.resolve(issued.access_token, now=1000.0) is None
    assert store.refresh(issued.refresh_token, now=1000.0) is None
    assert store.resolve(rotated.access_token, now=1000.0) is not None
