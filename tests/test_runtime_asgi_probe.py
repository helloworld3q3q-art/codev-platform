"""无监听端口 ASGI 探针适配器测试。"""

from __future__ import annotations

import json

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from codev_platform.runtime_asgi_probe import AsgiProbeError, post_asgi_request


def test_进程内请求完整执行真实asgi路由() -> None:
    async def handle(request):
        return JSONResponse(
            {
                "body": (await request.body()).decode("utf-8"),
                "signature": request.headers["x-signature"],
            }
        )
    app = Starlette(routes=[Route("/probe", handle, methods=["POST"])])

    response = post_asgi_request(
        app,
        path="/probe",
        body=b"payload",
        headers=(("content-type", "application/json"), ("x-signature", "signed")),
        max_response_bytes=4096,
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"body": "payload", "signature": "signed"}


def test_响应超过固定上限时失败关闭() -> None:
    async def handle(_request):
        return Response(b"x" * 32)

    app = Starlette(routes=[Route("/probe", handle, methods=["POST"])])

    with pytest.raises(AsgiProbeError, match="响应超过上限"):
        post_asgi_request(
            app,
            path="/probe",
            body=b"",
            headers=(),
            max_response_bytes=16,
        )
