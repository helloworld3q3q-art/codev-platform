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
from tests.runtime_identity_support import fake_runtime_identity  # noqa: E402


_TARGET_COMMIT = "d" * 40


def _client():
    return TestClient(server.build_app(identity_loader=fake_runtime_identity))


def test_oversized_body_rejected_413():
    big = b"x" * (server._MAX_BODY + 1)
    resp = _client().post("/gitea", content=big)
    assert resp.status_code == 413
    assert resp.json().get("error") == "payload too large"


def test_oversized_body_does_not_enqueue(monkeypatch):
    class _Q:
        def enqueue(self, pid, kind, meta=None):  # pragma: no cover
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
    child = tmp_path / "child"
    child.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind, meta=None):
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
        "after": _TARGET_COMMIT,
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
    child = tmp_path / "child"
    child.mkdir()
    parent = tmp_path / "parent"
    parent.mkdir()
    secret = "unit-test-secret"
    enq: list[tuple[str, str]] = []

    class _Q:
        def enqueue(self, pid, kind, meta=None):
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
        "after": _TARGET_COMMIT,
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    body = json.dumps(payload).encode("utf-8")
    resp = _client().post("/gitea", content=body, headers=_signed_gitea_headers(body, secret))

    assert resp.status_code == 200
    assert ("child-proj", "codegraph") in enq
    assert ("parent-proj", "codegraph") in enq


def test_webhook_bad_signature_does_not_enqueue(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()

    class _Q:
        def enqueue(self, pid, kind, meta=None):  # pragma: no cover
            raise AssertionError("bad signature must not enqueue")

    cfg = {
        "webhook": {"secret": "right-secret"},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "after": _TARGET_COMMIT,
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    body = json.dumps(payload).encode("utf-8")
    resp = _client().post("/gitea", content=body, headers=_signed_gitea_headers(body, "wrong-secret"))

    assert resp.status_code == 401


def test_webhook_queue_open_failure_returns_503(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)

    def boom(*args, **kwargs):
        raise RuntimeError("pg down")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", boom)

    payload = {
        "after": _TARGET_COMMIT,
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
    child = tmp_path / "child"
    child.mkdir()
    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)

    class _Q:
        def enqueue(self, pid, kind, meta=None):
            raise RuntimeError("enqueue failed")

    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "after": _TARGET_COMMIT,
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


def test_gitea_provider_parses_after_as_target_commit():
    from codev_platform.webhook.providers import GiteaProvider

    event = GiteaProvider().parse(
        {"X-Gitea-Event": "push"},
        {
            "after": _TARGET_COMMIT,
            "repository": {"full_name": "org/child"},
            "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
        },
    )

    assert event is not None
    assert event.target_commit == _TARGET_COMMIT


def test_webhook_enqueues_ff_only_meta_with_target_commit(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    enq: list[tuple[str, str, object]] = []

    class _Q:
        def enqueue(self, pid, kind, meta=None):
            enq.append((pid, kind, meta))

    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())

    payload = {
        "after": _TARGET_COMMIT,
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }
    resp = _client().post(
        "/gitea",
        content=json.dumps(payload).encode("utf-8"),
        headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    assert [kind for _pid, kind, _meta in enq] == ["codegraph", "ingest", "code_vec"]
    assert all(meta.source == "webhook" for _pid, _kind, meta in enq)
    assert all(meta.pull_policy == "ff_only" for _pid, _kind, meta in enq)
    assert all(meta.target_commit == _TARGET_COMMIT for _pid, _kind, meta in enq)


@pytest.mark.parametrize("target_commit", [None, "HEAD", "deadbeef", "D" * 40, "0" * 40])
def test_webhook_拒绝非法目标提交且不入队(tmp_path, monkeypatch, target_commit):
    child = tmp_path / "child"
    child.mkdir()

    class _Q:
        def enqueue(self, pid, kind, meta=None):  # pragma: no cover
            raise AssertionError("非法目标提交不得入队")

    cfg = {
        "webhook": {"allow_insecure": True},
        "projects": {"child-proj": {"repo_path": str(child), "webhook_repo": "org/child"}},
    }
    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", lambda **kwargs: _Q())
    payload = {
        "after": target_commit,
        "repository": {"full_name": "org/child"},
        "commits": [{"modified": ["apps/web/src/Foo.java"], "added": [], "removed": []}],
    }

    resp = _client().post(
        "/gitea",
        content=json.dumps(payload).encode("utf-8"),
        headers={"X-Gitea-Event": "push", "Content-Type": "application/json"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid target commit"


def test_webhook_mapping_warnings_for_unmapped_extra_repo(tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    parent.mkdir()
    child = tmp_path / "child"
    child.mkdir()
    cfg = {
        "projects": {
            "parent-proj": {
                "repo_path": str(parent),
                "webhook_repo": "org/parent",
                "extra_repos": [str(child)],
            },
        }
    }
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})

    warnings = server.webhook_mapping_warnings(cfg)

    assert len(warnings) == 1
    assert "parent-proj" in warnings[0]
    assert "未映射" in warnings[0]


def test_webhook_mapping_warnings_scan_webhook_enabled_projects_only(monkeypatch):
    cfg = {"projects": {"active-proj": {}, "hooked-proj": {"webhook_repo": "org/hooked"}}}
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["meta-child"]} if pid == "meta-parent" else {},
    )

    assert server.webhook_mapping_warnings(cfg) == []


def test_webhook_mapping_warnings_skip_project_without_webhook_repo(monkeypatch):
    cfg = {"projects": {"sample-project-gamma": {"repo_path": "/abs/oms"}}}
    monkeypatch.setattr(
        "codev_platform.core.repos._read_meta",
        lambda pid: {"extra_repos": ["/abs/child"]} if pid == "sample-project-gamma" else {},
    )

    assert server.webhook_mapping_warnings(cfg) == []


def test_webhook_mapping_warnings_keep_enabled_project_warning(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    cfg = {
        "projects": {
            "parent-proj": {
                "repo_path": str(tmp_path / "parent"),
                "webhook_repo": "org/parent",
                "extra_repos": [str(child)],
            }
        }
    }
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda pid: {})

    warnings = server.webhook_mapping_warnings(cfg)

    assert len(warnings) == 1
    assert "parent-proj" in warnings[0]
