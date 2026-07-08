from __future__ import annotations

import json
import urllib.error

from codev_platform.ops import health as health_mod


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"projects": {}, "registered": [], "memory_org": 0}).encode("utf-8")


def _patch_config(monkeypatch, cfg=None):
    monkeypatch.setattr(health_mod, "load_cfg", lambda: cfg or {"platform": {"url": "http://platform.local"}})


def test_health_all_sends_platform_token_header(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.setenv("PLATFORM_TOKEN", "primary-token")
    monkeypatch.setenv("CODEV_PLATFORM_MCP_TOKEN", "secondary-token")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["timeout"] = timeout
        return _Resp()

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 0

    assert seen == {
        "url": "http://platform.local/platform/status",
        "auth": "Bearer primary-token",
        "timeout": 20,
    }


def test_health_all_falls_back_to_codex_mcp_token(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.delenv("PLATFORM_TOKEN", raising=False)
    monkeypatch.setenv("CODEV_PLATFORM_MCP_TOKEN", "codex-token")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 0
    assert seen["auth"] == "Bearer codex-token"


def test_health_all_retries_next_token_env_after_401(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.setenv("PLATFORM_TOKEN", "stale-token")
    monkeypatch.setenv("CODEV_PLATFORM_MCP_TOKEN", "fresh-token")
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.get_header("Authorization"))
        if req.get_header("Authorization") == "Bearer stale-token":
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)
        return _Resp()

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 0
    assert seen == ["Bearer stale-token", "Bearer fresh-token"]


def test_health_all_without_token_sends_no_authorization(monkeypatch):
    _patch_config(monkeypatch)
    monkeypatch.delenv("PLATFORM_TOKEN", raising=False)
    monkeypatch.delenv("CODEV_PLATFORM_MCP_TOKEN", raising=False)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 0
    assert seen["auth"] is None


def test_health_all_prefers_configured_token_env(monkeypatch):
    _patch_config(
        monkeypatch,
        {"platform": {"url": "http://platform.local", "token_env": "CUSTOM_PLATFORM_TOKEN"}},
    )
    monkeypatch.setenv("CUSTOM_PLATFORM_TOKEN", "custom-token")
    monkeypatch.setenv("PLATFORM_TOKEN", "primary-token")
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        return _Resp()

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 0
    assert seen["auth"] == "Bearer custom-token"


def test_health_all_401_mentions_token_envs_without_leaking_token(monkeypatch, capsys):
    _patch_config(monkeypatch)
    monkeypatch.setenv("PLATFORM_TOKEN", "do-not-print-me")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr(health_mod.urllib.request, "urlopen", fake_urlopen)

    assert health_mod.cmd_health_all(object()) == 1
    captured = capsys.readouterr().out
    assert "PLATFORM_TOKEN" in captured
    assert "CODEV_PLATFORM_MCP_TOKEN" in captured
    assert "do-not-print-me" not in captured
