"""CodeGraph 受控恢复的纯 HTTP 健康证明测试。"""

from __future__ import annotations

import pytest


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self._body


def test_健康证明只访问公开healthz并要求精确成功响应() -> None:
    from codev_platform.ops.reindex_codegraph_resume_health import (
        prove_codegraph_health,
        require_codegraph_health_port,
    )

    requests: list[object] = []

    def opener(request, *, timeout: float):
        requests.append((request, timeout))
        return _Response(200, b'{"status":"ok","service":"codegraph"}')

    prove_codegraph_health(
        "http://127.0.0.1:19091/healthz",
        opener=opener,
        timeout_sec=0.0,
    )

    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:19091/healthz"
    assert request.method == "GET"
    assert timeout > 0
    assert require_codegraph_health_port(request.full_url) == 19091


@pytest.mark.parametrize(
    "response",
    [
        _Response(503, b'{"status":"ok","service":"codegraph"}'),
        _Response(200, b'{"status":"not-ok","service":"codegraph"}'),
        _Response(200, b"not-json"),
    ],
)
def test_健康证明拒绝非成功健康响应(response: _Response) -> None:
    from codev_platform.ops.reindex_codegraph_resume_health import (
        CodegraphResumeHealthError,
        prove_codegraph_health,
    )

    with pytest.raises(CodegraphResumeHealthError):
        prove_codegraph_health(
            "http://127.0.0.1:18091/healthz",
            opener=lambda _request, *, timeout: response,
            timeout_sec=0.0,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:18091/health",
        "http://localhost:18091/healthz",
        "https://127.0.0.1:18091/healthz",
        "http://127.0.0.1:18091/healthz?fallback=1",
    ],
)
def test_健康证明拒绝非唯一公开healthz地址(url: str) -> None:
    from codev_platform.ops.reindex_codegraph_resume_health import (
        CodegraphResumeHealthError,
        prove_codegraph_health,
    )

    with pytest.raises(CodegraphResumeHealthError):
        prove_codegraph_health(
            url,
            opener=lambda *_args, **_kwargs: pytest.fail("非法地址不得发起请求"),
            timeout_sec=0.0,
        )
