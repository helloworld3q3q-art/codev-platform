"""webhook 请求体大小上限 (审计 #7) —— 超 _MAX_BODY 返回 413, 不进 reindex 队列。

starlette.TestClient 会按 body 自动带 Content-Length, 命中 server 的早拒分支。
"""
from __future__ import annotations

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


def test_normal_size_passes_body_limit():
    # 小 body 不触发 413 (走到后续验签/解析; 无 secret + 默认 fail-closed → 401, 不是 413)。
    resp = _client().post("/gitea", content=b'{"ok": true}')
    assert resp.status_code != 413


def test_unknown_provider_404_before_body_check():
    resp = _client().post("/nope", content=b"x")
    assert resp.status_code == 404
