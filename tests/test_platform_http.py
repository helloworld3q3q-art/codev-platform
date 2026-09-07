from __future__ import annotations

import json
import urllib.error
import urllib.request

from codev_platform.core import platform_http


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_authenticated_json_request_retries_tokens_and_preserves_request_contract(monkeypatch):
    cfg = {"platform": {"token_env": "PRIMARY_TOKEN"}}
    monkeypatch.setenv("PRIMARY_TOKEN", "stale-token")
    monkeypatch.setenv("PLATFORM_TOKEN", "fresh-token")
    seen: list[tuple[str | None, str, str | None]] = []

    def opener(request, timeout=None):
        auth = request.get_header("Authorization")
        seen.append((auth, request.get_method(), request.get_header("X-project-id")))
        if auth == "Bearer stale-token":
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "Unauthorized",
                hdrs=None,
                fp=None,
            )
        assert timeout == 7
        return _Response({"result": 0, "data": {"ok": True}})

    payload = platform_http.authenticated_json_request(
        "http://platform.local/api/status",
        cfg,
        method="POST",
        headers={"X-Project-Id": "demo"},
        timeout=7,
        opener=opener,
    )

    assert payload == {"result": 0, "data": {"ok": True}}
    assert seen == [
        ("Bearer stale-token", "POST", "demo"),
        ("Bearer fresh-token", "POST", "demo"),
    ]


def test_authenticated_json_request_does_not_mutate_caller_headers(monkeypatch):
    monkeypatch.setenv("PLATFORM_TOKEN", "secret-token")
    headers = {"X-Project-Id": "demo"}

    platform_http.authenticated_json_request(
        "http://platform.local/api/status",
        {},
        headers=headers,
        opener=lambda _request, timeout=None: _Response({}),
    )

    assert headers == {"X-Project-Id": "demo"}


def test_bearer_header_is_not_forwarded_by_urllib_redirect(monkeypatch):
    monkeypatch.setenv("PLATFORM_TOKEN", "secret-token")
    _name, request = platform_http.authenticated_requests(
        "http://platform.local/api/status",
        {},
    )[0]

    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "http://attacker.invalid/collect",
    )

    assert request.get_header("Authorization") == "Bearer secret-token"
    assert redirected is not None
    assert redirected.get_header("Authorization") is None


def test_platform_web_url_uses_explicit_url_before_owner_defaults(monkeypatch):
    cfg = {
        "platform": {"web_url": "https://platform.example/control/"},
        "web": {"port": 18088},
    }
    monkeypatch.setattr(platform_http, "wsl_data_owner", lambda _cfg: object())

    assert platform_http.platform_web_url(cfg) == "https://platform.example/control"


def test_platform_web_url_uses_platform_source_host_for_wsl_owner(monkeypatch):
    cfg = {
        "mcp_sources": {"platform": {"host": "127.0.0.2"}},
        "web": {"port": 28088},
    }
    monkeypatch.setattr(platform_http, "wsl_data_owner", lambda _cfg: object())

    assert platform_http.platform_web_url(cfg) == "http://127.0.0.2:28088"
