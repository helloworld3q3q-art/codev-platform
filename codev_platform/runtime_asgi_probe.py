"""不开放网络监听端口的受限 ASGI 请求适配器。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


class AsgiProbeError(RuntimeError):
    """进程内 ASGI 探针无法形成有界、完整的 HTTP 响应。"""


@dataclass(frozen=True, slots=True)
class AsgiProbeResponse:
    """探针只保留状态码与受限响应正文。"""

    status_code: int
    body: bytes


def post_asgi_request(
    app: Any,
    *,
    path: str,
    body: bytes,
    headers: tuple[tuple[str, str], ...],
    max_response_bytes: int,
) -> AsgiProbeResponse:
    """通过 ASGI 协议直接执行单次 POST，不绑定套接字或启动后台任务。"""
    _require_request(app, path, body, headers, max_response_bytes)
    try:
        return asyncio.run(
            _post_asgi_request(
                app,
                path=path,
                body=body,
                headers=headers,
                max_response_bytes=max_response_bytes,
            )
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except AsgiProbeError:
        raise
    except Exception:
        raise AsgiProbeError("ASGI 探针执行失败") from None


async def _post_asgi_request(
    app: Any,
    *,
    path: str,
    body: bytes,
    headers: tuple[tuple[str, str], ...],
    max_response_bytes: int,
) -> AsgiProbeResponse:
    request_sent = False
    status_code: int | None = None
    chunks: list[bytes] = []
    response_bytes = 0
    response_complete = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        nonlocal response_bytes, response_complete, status_code
        message_type = message.get("type")
        if message_type == "http.response.start":
            candidate = message.get("status")
            if status_code is not None or type(candidate) is not int or not 100 <= candidate <= 599:
                raise AsgiProbeError("ASGI 探针响应起始帧无效")
            status_code = candidate
            return
        if message_type != "http.response.body" or status_code is None or response_complete:
            raise AsgiProbeError("ASGI 探针响应帧无效")
        chunk = message.get("body", b"")
        more_body = message.get("more_body", False)
        if type(chunk) is not bytes or type(more_body) is not bool:
            raise AsgiProbeError("ASGI 探针响应正文无效")
        response_bytes += len(chunk)
        if response_bytes > max_response_bytes:
            raise AsgiProbeError("ASGI 探针响应超过上限")
        chunks.append(chunk)
        response_complete = not more_body

    encoded_headers = [
        (name.lower().encode("ascii"), value.encode("latin-1")) for name, value in headers
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "root_path": "",
        "headers": encoded_headers,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 80),
        "state": {},
    }
    await app(scope, receive, send)
    if status_code is None or response_complete is not True:
        raise AsgiProbeError("ASGI 探针响应不完整")
    return AsgiProbeResponse(status_code=status_code, body=b"".join(chunks))


def _require_request(
    app: Any,
    path: str,
    body: bytes,
    headers: tuple[tuple[str, str], ...],
    max_response_bytes: int,
) -> None:
    if not callable(app):
        raise AsgiProbeError("ASGI 探针应用不可用")
    if (
        type(path) is not str
        or not path.startswith("/")
        or "?" in path
        or "#" in path
        or type(body) is not bytes
        or type(headers) is not tuple
        or type(max_response_bytes) is not int
        or not 0 < max_response_bytes <= 1024 * 1024
    ):
        raise AsgiProbeError("ASGI 探针请求参数无效")
    try:
        for item in headers:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
                or not item[0]
            ):
                raise AsgiProbeError("ASGI 探针请求头无效")
            item[0].encode("ascii")
            item[1].encode("latin-1")
    except (UnicodeEncodeError, TypeError, ValueError):
        raise AsgiProbeError("ASGI 探针请求头无效") from None


__all__ = ["AsgiProbeError", "AsgiProbeResponse", "post_asgi_request"]
