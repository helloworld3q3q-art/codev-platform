"""Webhook 开放后对真实 loopback 监听和 HTTP 路由执行验收。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_deployment_contract import RuntimeDeploymentError


_MAX_RESPONSE_BYTES = 64 * 1024
HttpRequest = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class WebhookHttpEvidence:
    evidence_sha256: str

    def __post_init__(self) -> None:
        try:
            require_sha256(self.evidence_sha256, field="webhook_http_evidence")
        except ValueError:
            raise RuntimeDeploymentError("Webhook 真实入口证据无效") from None


def verify_webhook_http(
    config: dict,
    *,
    request: HttpRequest | None = None,
) -> WebhookHttpEvidence:
    """只访问受管端口的 loopback 健康路由，拒绝代理、重定向和宽松响应。"""
    if type(config) is not dict:
        raise RuntimeDeploymentError("Webhook 真实入口配置无效")
    try:
        from codev_platform.webhook.server import webhook_port

        port = webhook_port(config)
        if type(port) is not int or not 1 <= port <= 65_535:
            raise ValueError("端口无效")
        url = f"http://127.0.0.1:{port}/healthz"
        response = _request(url) if request is None else request(url)
        content = getattr(response, "content", None)
        if (
            getattr(response, "status_code", None) != 200
            or type(content) is not bytes
            or not content
            or len(content) > _MAX_RESPONSE_BYTES
        ):
            raise ValueError("响应无效")
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        expected = {"service": "webhook", "status": "ok"}
        if type(payload) is not dict or payload != expected:
            raise ValueError("响应契约无效")
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeDeploymentError("Webhook 真实入口验收失败") from None
    evidence = hashlib.sha256(
        f"127.0.0.1\n{port}\nwebhook\nok\n".encode("ascii")
    ).hexdigest()
    return WebhookHttpEvidence(evidence)


def _request(url: str) -> object:
    import httpx

    timeout = httpx.Timeout(5.0, connect=2.0)
    with httpx.Client(
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    ) as client:
        return client.get(url, headers={"Accept": "application/json"})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("JSON 常量无效")


__all__ = ["WebhookHttpEvidence", "verify_webhook_http"]
