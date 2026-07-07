"""webhook 请求体大小上限 (审计 #7) —— 超 _MAX_BODY 返回 413, 不进 reindex 队列。

starlette.TestClient 会按 body 自动带 Content-Length, 命中 server 的早拒分支。
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from codev_platform.webhook import server  # noqa: E402


def _client():
    return TestClient(server.build_app())


def test_oversized_body_rejected_413():
    big = b"x" * (server._MAX_BODY + 1)
    resp = _client().post("/gitea", content=big)
    assert resp.status_code == 413
    assert resp.json().get("error") == "payload too large"


def test_oversized_body_does_not_enqueue(monkeypatch):
    class _Q:
        def enqueue(self, pid, kind):  # pragma: no cover
            raise AssertionError("oversized body must not enqueue")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())
    big = b"x" * (server._MAX_BODY + 1)
    resp = _client().post("/gitea", content=big)
    assert resp.status_code == 413


def test_normal_size_passes_body_limit():
    # 小 body 不触发 413 (走到后续验签/解析; 无 secret + 默认 fail-closed → 401, 不是 413)。
    resp = _client().post("/gitea", content=b'{"ok": true}')
    assert resp.status_code != 413


def test_unknown_provider_404_before_body_check():
    resp = _client().post("/nope", content=b"x")
    assert resp.status_code == 404


def test_webhook_extra_repo_enqueues_parent_project(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()
    parent = tmp_path / "parent"; parent.mkdir()
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind):
            enq.append((pid, kind))

    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {
            "child-proj": {"repo_path": str(child), "webhook_repo": "org/child"},
            "parent-proj": {"repo_path": str(parent), "extra_repos": [str(child)]},
        },
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    resp = _client().post(
        "/gitea",
        content=json.dumps(payload).encode("utf-8"),
        headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert ("child-proj", "codegraph") in enq
    assert ("parent-proj", "codegraph") in enq
    parent_order = [kind for pid, kind in enq if pid == "parent-proj"]
    assert parent_order == ["codegraph", "ingest", "code_vec"]


def _signed_gitea_headers(body: bytes, secret: str) -> dict[str, str]:
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return {
        "X-Gitea-Event": "push",
        "X-Gitea-Signature": sig,
        "Content-Type": "application/json",
    }


def test_webhook_signed_push_enqueues_parent_project(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()
    parent = tmp_path / "parent"; parent.mkdir()
    secret = "unit-test-secret"
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind):
            enq.append((pid, kind))

    cfg = {
        "webhook": {"secret": secret},
        "projects": {
            "child-proj": {"repo_path": str(child), "webhook_repo": "org/child"},
            "parent-proj": {"repo_path": str(parent), "extra_repos": [str(child)]},
        },
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    body = json.dumps(payload).encode("utf-8")
    resp = _client().post("/gitea", content=body, headers=_signed_gitea_headers(body, secret))

    assert resp.status_code == 200
    assert ("child-proj", "codegraph") in enq
    assert ("parent-proj", "codegraph") in enq


def test_webhook_bad_signature_does_not_enqueue(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()

    class _Q:
        def enqueue(self, pid, kind):  # pragma: no cover
            raise AssertionError("bad signature must not enqueue")

    cfg = {
        "webhook": {"secret": "right-secret"},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    body = json.dumps(payload).encode("utf-8")
    resp = _client().post("/gitea", content=body, headers=_signed_gitea_headers(body, "wrong-secret"))

    assert resp.status_code == 401


def test_webhook_queue_open_failure_returns_503(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()
    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)

    def boom(*args, **kwargs):
        raise RuntimeError("pg down")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", boom)

    payload = {
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    resp = _client().post(
        "/gitea",
        content=json.dumps(payload).encode("utf-8"),
        headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "reindex queue unavailable"


def test_open_default_queue_strict_pg_without_dsn_raises(monkeypatch):
    from codev_platform.reindex import open_default_queue
    monkeypatch.delenv("CODEV_PLATFORM_MEMORY_DSN", raising=False)
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: {"reindex": {"queue_backend": "pg"}, "memory": {"pg_dsn": None}},
    )

    with pytest.raises(RuntimeError, match="queue_backend=pg"):
        open_default_queue(fail_soft=False)


def test_webhook_enqueue_failure_returns_503(tmp_path, monkeypatch):
    child = tmp_path / "child"; child.mkdir()
    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)

    class _Q:
        def enqueue(self, pid, kind):
            raise RuntimeError("enqueue failed")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    resp = _client().post(
        "/gitea",
        content=json.dumps(payload).encode("utf-8"),
        headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "reindex enqueue failed"


def test_webhook_mapping_warnings_for_unmapped_extra_repo(tmp_path, monkeypatch):
    parent = tmp_path / "parent"; parent.mkdir()
    child = tmp_path / "child"; child.mkdir()
    cfg = {
        "projects": {
            "parent-proj": {"repo_path": str(parent), "extra_repos": [str(child)]},
        }
    }
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})

    warnings = server.webhook_mapping_warnings(cfg)

    assert len(warnings) == 1
    assert "parent-proj" in warnings[0]
    assert "未映射" in warnings[0]
