"""受保护运行身份状态面的路由、认证与启动缓存测试。"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.gateway import PassthroughAuthenticator, TokenAuthenticator
from codev_platform.gateway.auth import token_hash
from tests.runtime_identity_support import RUNTIME_REVISION, fake_runtime_identity

_TOKEN = "runtime-status-token"


class _IdentityProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.value = fake_runtime_identity()

    def __call__(self) -> RuntimeIdentity:
        self.calls += 1
        return self.value


def _token_config() -> dict[str, object]:
    return {
        "gateway": {
            "auth_mode": "token",
            "tokens": {
                token_hash(_TOKEN): {
                    "user_id": "runtime-auditor",
                    "org_id": "platform",
                    "projects": "*",
                }
            },
        }
    }


def _get(app, path: str, *, token: str | None = None) -> tuple[int, dict[str, object]]:
    async def invoke() -> tuple[int, dict[str, object]]:
        headers = []
        if token is not None:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 23000),
            "server": ("127.0.0.1", 80),
            "state": {},
        }
        messages: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            messages.append(message)

        await app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        return int(start["status"]), json.loads(body)

    return asyncio.run(invoke())


def _capture_http_app(monkeypatch) -> list[object]:
    import uvicorn

    captured: list[object] = []

    class CaptureServer:
        def __init__(self, config) -> None:
            captured.append(config.app)

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(uvicorn, "Server", CaptureServer)
    return captured


@pytest.mark.parametrize(
    ("module_name", "runner_name"),
    [
        ("codev_platform.chroma.server", "_run_http"),
        ("codev_platform.codegraph.server", "run_http"),
        ("codev_platform.graph.mcp_server", "run_http"),
        ("codev_platform.agent.memory_mcp", "run_http"),
    ],
)
def test_mcp_status_is_protected_and_caches_one_runtime_identity(
    monkeypatch,
    module_name: str,
    runner_name: str,
) -> None:
    from codev_platform.core import config as config_module
    from codev_platform.core import runtime_identity as identity_module
    from codev_platform import mcp_serve, platform_status

    module = importlib.import_module(module_name)
    probe = _IdentityProbe()
    cfg = _token_config()
    monkeypatch.setattr(identity_module, "runtime_identity", probe)
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    if hasattr(module, "load_config"):
        monkeypatch.setattr(module, "load_config", lambda: cfg)
    monkeypatch.setattr(mcp_serve, "mcp_bind_host", lambda _cfg: "127.0.0.1")
    monkeypatch.setattr(
        platform_status,
        "build_platform_status",
        lambda _cfg, **_kwargs: {"status": "ok", "service": "platform-docs"},
    )
    if module_name.endswith("codegraph.server"):
        monkeypatch.setattr(
            module._maintenance_gate,
            "require_codegraph_service_start_permitted",
            lambda: None,
        )
    if module_name.endswith("memory_mcp"):
        from codev_platform.agent import deps

        monkeypatch.setattr(deps, "get_memory_store", lambda: object())
        monkeypatch.setattr(deps, "get_rbac_store", lambda: object())
    captured = _capture_http_app(monkeypatch)

    asyncio.run(getattr(module, runner_name)(0))

    assert probe.calls == 1
    assert len(captured) == 1
    health_status, health = _get(captured[0], "/healthz")
    assert health_status in {200, 503}
    assert "runtime" not in health
    denied_status, denied = _get(captured[0], "/platform/status")
    assert (denied_status, denied) == (401, {"error": "unauthorized"})
    status, body = _get(captured[0], "/platform/status", token=_TOKEN)
    assert status == 200
    assert body["runtime"]["runtime_revision"] == RUNTIME_REVISION
    assert probe.calls == 1


def test_webhook_status_has_dedicated_auth_without_opening_platform_provider(
    monkeypatch,
) -> None:
    from codev_platform.webhook import providers
    from codev_platform.webhook import server

    probe = _IdentityProbe()
    monkeypatch.setattr(providers, "names", lambda: ["platform"])
    authenticator = TokenAuthenticator(
        {token_hash(_TOKEN): {"user_id": "auditor", "org_id": "platform"}}
    )

    app = server.build_app(
        status_authenticator=authenticator,
        identity_loader=probe,
    )

    assert probe.calls == 1
    assert _get(app, "/healthz") == (200, {"status": "ok", "service": "webhook"})
    assert _get(app, "/platform/status") == (401, {"error": "unauthorized"})
    status, body = _get(app, "/platform/status", token=_TOKEN)
    assert status == 200
    assert body["providers"] == ["platform"]
    assert body["runtime"]["runtime_revision"] == RUNTIME_REVISION
    assert probe.calls == 1

    local_app = server.build_app(
        status_authenticator=PassthroughAuthenticator(),
        identity_loader=probe,
    )
    assert _get(local_app, "/platform/status")[0] == 200
    assert probe.calls == 2


def test_agent_status_is_protected_and_health_remains_public(monkeypatch) -> None:
    from codev_platform.core import config as config_module

    cfg = _token_config()
    probe = _IdentityProbe()
    monkeypatch.setattr(config_module, "load_config", lambda: cfg)
    service = importlib.import_module("codev_platform.agent.service")

    app = service.create_app(identity_loader=probe)

    assert probe.calls == 1
    health_status, health = _get(app, "/health")
    assert health_status == 200
    assert "runtime" not in health
    assert _get(app, "/platform/status") == (401, {"error": "unauthorized"})
    status, body = _get(app, "/platform/status", token=_TOKEN)
    assert status == 200
    assert body["service"] == "agent"
    assert body["runtime"]["runtime_revision"] == RUNTIME_REVISION
    assert probe.calls == 1

    monkeypatch.setattr(config_module, "load_config", lambda: {})
    local_app = service.create_app(identity_loader=probe)
    assert _get(local_app, "/platform/status")[0] == 200


def test_web_runtime_status_is_protected_and_not_a_public_health_path(monkeypatch) -> None:
    probe = _IdentityProbe()
    web_app = importlib.import_module("codev_platform.web.app")
    app = web_app.create_app(cfg=_token_config(), identity_loader=probe)

    assert probe.calls == 1
    assert "/api/v1/runtime/status" not in web_app._PUBLIC_PATHS
    assert _get(app, "/health") == (200, {"status": "ok"})
    assert _get(app, "/api/v1/runtime/status") == (401, {"error": "unauthorized"})
    status, body = _get(app, "/api/v1/runtime/status", token=_TOKEN)
    assert status == 200
    assert body["service"] == "web"
    assert body["runtime"]["runtime_revision"] == RUNTIME_REVISION
    assert probe.calls == 1


@pytest.mark.parametrize(
    "module_name",
    ["codev_platform.web.app", "codev_platform.agent.service"],
)
def test_http_app_module_import_does_not_resolve_runtime_identity(
    monkeypatch,
    module_name: str,
) -> None:
    from codev_platform.core import runtime_identity as identity_module

    def unexpected_identity_resolution() -> RuntimeIdentity:
        raise AssertionError("模块导入阶段不得解析运行身份")

    monkeypatch.setattr(
        identity_module,
        "runtime_identity",
        unexpected_identity_resolution,
    )
    module = importlib.import_module(module_name)

    importlib.reload(module)


@pytest.mark.parametrize(
    "module_name",
    ["codev_platform.web.app", "codev_platform.agent.service"],
)
def test_production_http_app_factory_resolves_runtime_identity_once(
    monkeypatch,
    module_name: str,
) -> None:
    from codev_platform.core import config as config_module
    from codev_platform.core import runtime_identity as identity_module

    probe = _IdentityProbe()
    monkeypatch.setattr(identity_module, "runtime_identity", probe)
    monkeypatch.setattr(config_module, "load_config", lambda: {})
    module = importlib.import_module(module_name)

    app = module.create_runtime_app()

    assert probe.calls == 1
    assert app.state.runtime_identity is probe.value


def test_web_entrypoints_use_explicit_runtime_factory(monkeypatch) -> None:
    from codev_platform import cli
    from codev_platform.core import config as config_module
    from codev_platform.web import config as web_config
    from codev_platform.web import main as web_main

    calls: list[tuple[str, dict[str, object]]] = []
    fake_uvicorn = SimpleNamespace(
        run=lambda target, **kwargs: calls.append((target, kwargs)),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(web_config, "web_host", lambda _cfg: "127.0.0.1")
    monkeypatch.setattr(web_config, "web_port", lambda _cfg: 18088)
    monkeypatch.setattr(web_config, "web_tls", lambda _cfg: None)

    assert cli.cmd_web(SimpleNamespace(host="127.0.0.1", port=18088)) == 0
    web_main.main()

    assert calls == [
        (
            "codev_platform.web.app:create_runtime_app",
            {"host": "127.0.0.1", "port": 18088, "log_level": "info", "factory": True},
        ),
        (
            "codev_platform.web.app:create_runtime_app",
            {"host": "127.0.0.1", "port": 18088, "factory": True},
        ),
    ]


def test_agent_entrypoint_uses_explicit_runtime_factory(monkeypatch) -> None:
    from codev_platform.core import config as config_module
    from codev_platform.gateway import auth as auth_module
    from codev_platform.ops import agent as agent_ops

    calls: list[tuple[str, dict[str, object]]] = []
    fake_uvicorn = SimpleNamespace(
        run=lambda target, **kwargs: calls.append((target, kwargs)),
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(config_module, "load_config", lambda: {})
    monkeypatch.setattr(auth_module, "build_authenticator", lambda _cfg: object())
    monkeypatch.setattr(auth_module, "deploy_policy_error", lambda _cfg, _host: None)
    monkeypatch.setattr(auth_module, "multi_user_policy_error", lambda _cfg: None)
    monkeypatch.setattr(auth_module, "warn_if_insecure", lambda _auth, _host: None)
    args = SimpleNamespace(
        action="serve",
        host="127.0.0.1",
        port=8848,
        reload=False,
    )

    assert agent_ops.cmd_agent(args) == 0

    assert calls == [
        (
            "codev_platform.agent.service:create_runtime_app",
            {
                "host": "127.0.0.1",
                "port": 8848,
                "reload": False,
                "factory": True,
            },
        )
    ]
