"""platform-docs 多租户冷启动 readiness 回归。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from codev_platform.chroma import _readiness
from codev_platform.chroma import server
from tests.runtime_identity_support import fake_runtime_identity


def _http_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import uvicorn

    from codev_platform import mcp_serve
    from codev_platform.core import runtime_identity

    captured: list[object] = []

    class CaptureServer:
        def __init__(self, config) -> None:
            captured.append(config.app)

        async def serve(self) -> None:
            return None

    monkeypatch.setattr(server, "load_config", lambda: {})
    monkeypatch.setattr(mcp_serve, "mcp_bind_host", lambda _cfg: "127.0.0.1")
    monkeypatch.setattr(runtime_identity, "runtime_identity", fake_runtime_identity)
    monkeypatch.setattr(uvicorn, "Server", CaptureServer)
    asyncio.run(server._run_http(0))
    assert len(captured) == 1
    return TestClient(captured[0])


@pytest.mark.parametrize(
    ("model_ready", "default_project_id", "default_collection_ready", "expected"),
    (
        (True, None, False, True),
        (False, None, False, False),
        (True, "codev-platform", False, False),
        (True, "codev-platform", True, True),
    ),
)
def test_readiness区分多租户健康空态与默认项目未就绪(
    model_ready: bool,
    default_project_id: str | None,
    default_collection_ready: bool,
    expected: bool,
) -> None:
    assert _readiness.service_ready(
        model_ready=model_ready,
        default_project_id=default_project_id,
        default_collection_ready=default_collection_ready,
    ) is expected


def test_无默认项目只预热模型而不构造空租户() -> None:
    events: list[str] = []

    _readiness.prewarm_default_project(
        None,
        ensure_model=lambda: events.append("model"),
        ensure_project=lambda _project_id: events.append("project"),
    )

    assert events == ["model"]


def test_配置默认项目时仍预加载其collection() -> None:
    events: list[str] = []

    _readiness.prewarm_default_project(
        "codev-platform",
        ensure_model=lambda: events.append("model"),
        ensure_project=lambda project_id: events.append(f"project:{project_id}"),
    )

    assert events == ["project:codev-platform"]


def test_server当前readiness使用多租户策略(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.m, "_model", object())
    monkeypatch.setattr(server, "PROJECT_ID", None)
    monkeypatch.setattr(server, "_projects", {})

    assert server._current_readiness() == (True, False, True)

    monkeypatch.setattr(server, "PROJECT_ID", "codev-platform")
    assert server._current_readiness() == (True, False, False)

    monkeypatch.setattr(
        server,
        "_projects",
        {"codev-platform": SimpleNamespace(collection=object())},
    )
    assert server._current_readiness() == (True, True, True)


def test_默认项目不能被其它租户collection掩盖(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server.m, "_model", object())
    monkeypatch.setattr(server, "PROJECT_ID", "default-project")
    monkeypatch.setattr(
        server,
        "_projects",
        {"other-project": SimpleNamespace(collection=object())},
    )

    assert server._current_readiness() == (True, True, False)


def test_HTTP状态与平台聚合共享默认项目readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from codev_platform import platform_status

    observed: list[bool] = []
    monkeypatch.setattr(server.m, "_model", object())
    monkeypatch.setattr(server, "PROJECT_ID", "default-project")
    monkeypatch.setattr(
        server,
        "_projects",
        {"other-project": SimpleNamespace(collection=object())},
    )
    monkeypatch.setattr(
        platform_status,
        "build_platform_status",
        lambda _cfg, *, platform_docs_ready: observed.append(platform_docs_ready) or {},
    )
    client = _http_client(monkeypatch)

    assert client.get("/healthz").status_code == 503
    assert client.get("/platform/status").status_code == 200
    assert observed == [False]


def test_详细健康驱逐stale后同一响应降级(monkeypatch: pytest.MonkeyPatch) -> None:
    class StaleCollection:
        def count(self) -> int:
            raise RuntimeError("stale")

    monkeypatch.setattr(server.m, "_model", object())
    monkeypatch.setattr(server, "PROJECT_ID", "default-project")
    monkeypatch.setattr(
        server,
        "_projects",
        {
            "default-project": server._ProjectState(
                project_id="default-project", collection=StaleCollection()
            )
        },
    )
    monkeypatch.setattr(server, "_project_last_indexed_iso", lambda _pid: None)
    monkeypatch.setattr(server, "_process_info", lambda: {})
    monkeypatch.setattr(server, "_gpu_memory_mb", lambda: None)
    monkeypatch.setattr(server, "_gpu_free_info", lambda: {})
    response = _http_client(monkeypatch).get("/platform/health")

    assert response.status_code == 503
    assert response.json()["status"] == "starting"
    assert response.json()["collection"] == "init"
    assert server._projects == {}


@pytest.mark.parametrize(
    ("method", "path"),
    (("GET", "/mcp"), ("POST", "/mcp"), ("DELETE", "/mcp"), ("GET", "/sse")),
)
def test_无默认租户且请求缺project_id时不进入状态机(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(server, "PROJECT_ID", None)
    monkeypatch.setattr(
        server,
        "_ensure_project",
        lambda _pid: pytest.fail("空 project_id 不得进入状态机"),
    )

    response = _http_client(monkeypatch).request(method, path)

    assert response.status_code == 400
    assert response.json() == {"error": "project_id required"}
