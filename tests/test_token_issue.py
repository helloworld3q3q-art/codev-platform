"""共享 token 签发 issue_token —— CLI + web 共用, 明文只一次 + 存 hash 不存明文。"""
from __future__ import annotations

import time


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def issue(self, token_hash, user_id, org_id, *, projects, label, expires_at) -> None:
        self.calls.append(dict(token_hash=token_hash, user_id=user_id, org_id=org_id,
                               projects=projects, label=label, expires_at=expires_at))


def test_issue_token_returns_plaintext_and_stores_hash():
    from codev_platform.gateway.auth import token_hash
    from codev_platform.gateway.token_issue import issue_token
    store = _FakeStore()
    tok, exp = issue_token(store, "alice", "acme", projects="*", label="ide")
    assert exp is None
    assert len(store.calls) == 1
    c = store.calls[0]
    assert c["user_id"] == "alice" and c["org_id"] == "acme"
    assert c["projects"] == "*" and c["label"] == "ide"
    assert c["token_hash"] == token_hash(tok)   # 库存 hash
    assert c["token_hash"] != tok               # 明文不落库
    assert len(tok) > 20                         # 明文够长(secrets.token_urlsafe(32))


def test_issue_token_ttl_sets_future_expiry():
    from codev_platform.gateway.token_issue import issue_token
    store = _FakeStore()
    _tok, exp = issue_token(store, "u", "o", ttl_seconds=3600)
    assert exp is not None and exp > time.time() + 3000
    assert store.calls[0]["expires_at"] == exp


def test_issue_token_default_no_project_access():
    from codev_platform.gateway.token_issue import issue_token
    store = _FakeStore()
    issue_token(store, "u", "o")
    assert store.calls[0]["projects"] is None   # 无项目权 = 安全默认
