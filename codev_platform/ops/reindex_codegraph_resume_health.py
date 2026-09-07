"""CodeGraph 受控恢复后的仅 HTTP 健康证明。"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_REQUEST_TIMEOUT_SEC = 2.0
_RETRY_INTERVAL_SEC = 0.2
_MAX_RESPONSE_BYTES = 4096


class CodegraphResumeHealthError(RuntimeError):
    """CodeGraph 公开健康端点无法证明已就绪。"""


HttpOpener = Callable[..., object]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


def prove_codegraph_health(
    health_url: str,
    *,
    timeout_sec: float = 10.0,
    opener: HttpOpener | None = None,
    clock: Clock | None = None,
    sleeper: Sleeper | None = None,
) -> None:
    """有界轮询唯一公开 ``/healthz``，不回退 TCP、旧 health 或 MCP。"""
    require_codegraph_health_port(health_url)
    timeout = _require_timeout(timeout_sec)
    open_request = urlopen if opener is None else opener
    now = time.monotonic if clock is None else clock
    sleep = time.sleep if sleeper is None else sleeper
    if not all(callable(item) for item in (open_request, now, sleep)):
        raise CodegraphResumeHealthError("CodeGraph 健康证明适配器不可用")
    deadline = now() + timeout
    while True:
        if _health_response_ok(health_url, open_request):
            return
        remaining = deadline - now()
        if remaining <= 0:
            break
        sleep(min(_RETRY_INTERVAL_SEC, remaining))
    raise CodegraphResumeHealthError("CodeGraph 公开健康端点未就绪")


def require_codegraph_health_port(value: object) -> int:
    """校验恢复唯一健康地址，并返回其受控 loopback 端口。"""
    if type(value) is not str or not value:
        raise CodegraphResumeHealthError("CodeGraph 健康地址无效")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or port is None
            or not 1 <= port <= 65535
            or parsed.path != "/healthz"
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CodegraphResumeHealthError("CodeGraph 健康地址无效")
    except CodegraphResumeHealthError:
        raise
    except (TypeError, ValueError):
        raise CodegraphResumeHealthError("CodeGraph 健康地址无效") from None
    return port


def _require_timeout(value: object) -> float:
    if type(value) not in {int, float} or value < 0:
        raise CodegraphResumeHealthError("CodeGraph 健康证明超时无效")
    return float(value)


def _health_response_ok(health_url: str, opener: HttpOpener) -> bool:
    request = Request(health_url, method="GET")
    try:
        with opener(request, timeout=_REQUEST_TIMEOUT_SEC) as response:
            status = getattr(response, "status", None)
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if status != 200 or type(body) is not bytes or len(body) > _MAX_RESPONSE_BYTES:
            return False
        return json.loads(body.decode("utf-8", "strict")) == {
            "status": "ok",
            "service": "codegraph",
        }
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        return False


__all__ = [
    "CodegraphResumeHealthError",
    "prove_codegraph_health",
    "require_codegraph_health_port",
]
