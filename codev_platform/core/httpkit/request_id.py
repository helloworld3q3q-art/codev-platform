"""RequestIdMiddleware —— 纯 ASGI (仿 gateway/middleware.py: SSE 安全 + 高并发, 不缓冲)。

每请求生成或透传 X-Request-Id: 写进 scope["state"]["request_id"] (下游 request.state.request_id 可读),
并回写响应头 X-Request-Id。两服务 (agent / web) 共挂 (plan §九: 唯一真新增中间件)。

挂在最外层 (add_middleware 最后加): 让 401/限流/异常响应也能带上 request_id 锚点。
"""
from __future__ import annotations

import uuid

_HEADER = "x-request-id"
_MAX_LEN = 128


def _client_request_id(scope) -> str | None:
    """透传客户端传入的 X-Request-Id (限长 + 仅可见 ascii, 防注入/日志污染)。"""
    for k, v in scope.get("headers") or ():
        if k == _HEADER.encode("latin-1"):
            try:
                rid = v.decode("latin-1").strip()
            except Exception:  # noqa: BLE001
                return None
            if rid and len(rid) <= _MAX_LEN and rid.isprintable():
                return rid
    return None


class RequestIdMiddleware:
    """纯 ASGI: 生成/透传 request_id → scope.state + 响应头 X-Request-Id。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = _client_request_id(scope) or uuid.uuid4().hex
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["request_id"] = rid

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((_HEADER.encode("latin-1"), rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)
