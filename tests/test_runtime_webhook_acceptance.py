"""Webhook 真实 loopback 监听与 HTTP 路由验收测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


def _response(payload: object, *, status_code: int = 200) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        content=json.dumps(payload).encode("utf-8"),
    )


def test_真实入口验收只访问受管端口的loopback_healthz() -> None:
    from codev_platform.runtime_webhook_acceptance import verify_webhook_http

    urls: list[str] = []

    evidence = verify_webhook_http(
        {"webhook": {"port": 8765}},
        request=lambda url: urls.append(url)
        or _response({"service": "webhook", "status": "ok"}),
    )

    assert urls == ["http://127.0.0.1:8765/healthz"]
    assert len(evidence.evidence_sha256) == 64


@pytest.mark.parametrize(
    "response",
    (
        _response({"service": "webhook", "status": "starting"}),
        _response({"service": "other", "status": "ok"}),
        _response({"service": "webhook", "status": "ok"}, status_code=503),
        SimpleNamespace(status_code=200, content=b"not-json"),
    ),
)
def test_真实入口响应不是唯一健康契约时失败关闭(response: object) -> None:
    from codev_platform.runtime_webhook_acceptance import verify_webhook_http

    with pytest.raises(RuntimeDeploymentError, match="Webhook 真实入口验收失败"):
        verify_webhook_http(
            {"webhook": {"port": 8765}},
            request=lambda _url: response,
        )
