"""纯算力接口 (/embed /rerank) 本机 loopback 免 token, 远程仍鉴权。

背景: 平台切 token 模式后 daemon /embed 也要 token, 但内部 code_vec 索引 / agent-memory 写
经 RemoteEmbedder 调 /embed 不带 token → 401 → 向量 lane 全挂。修法不是把 /embed 设 public
(那远程能白嫖 GPU), 而是**仅本机 loopback 对端**免 token (无租户数据的纯算力), 远程仍 token。
"""
from __future__ import annotations

import asyncio

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from codev_platform.gateway.auth import TokenAuthenticator, token_hash
from codev_platform.gateway.middleware import AuthMiddleware, _client_is_loopback


def _run(app, path: str, client: tuple[str, int], headers: dict[str, str] | None = None) -> int:
    async def _go() -> int:
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": client, "server": ("127.0.0.1", 80), "state": {},
        }
        sent: list = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(m):
            sent.append(m)

        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")

    return asyncio.run(_go())


def _token_app():
    async def embed(_req):
        return JSONResponse({"vec": [1.0]})

    async def search(_req):
        return JSONResponse({"hits": []})

    app = Starlette(routes=[Route("/embed", embed, methods=["POST"]),
                            Route("/search_docs", search, methods=["POST"])])
    auth = TokenAuthenticator({token_hash("good-tok"): {"user_id": "svc", "org_id": "acme"}})
    app.add_middleware(AuthMiddleware, authenticator=auth,
                       public_paths={"/healthz"}, loopback_exempt_paths={"/embed", "/rerank"})
    return app


def test_embed_loopback_no_token_ok():
    """本机 loopback 调 /embed 不带 token → 放行 (内部索引复用 GPU)。"""
    app = _token_app()
    assert _run(app, "/embed", client=("127.0.0.1", 5000)) == 200
    assert _run(app, "/embed", client=("::1", 5000)) == 200


def test_embed_remote_no_token_rejected():
    """远程对端调 /embed 不带 token → 401 (防外部白嫖 GPU)。"""
    app = _token_app()
    assert _run(app, "/embed", client=("10.0.2.37", 5000)) == 401


def test_embed_remote_with_token_ok():
    """远程带合法 token → 放行 (正常多机鉴权路径)。"""
    app = _token_app()
    assert _run(app, "/embed", client=("10.0.2.37", 5000),
                headers={"Authorization": "Bearer good-tok"}) == 200


def test_data_endpoint_not_exempt_even_on_loopback():
    """数据接口 (/search_docs) 不在豁免集: 即使 loopback 也要 token (隔离红线)。"""
    app = _token_app()
    assert _run(app, "/search_docs", client=("127.0.0.1", 5000)) == 401
    assert _run(app, "/search_docs", client=("127.0.0.1", 5000),
                headers={"Authorization": "Bearer good-tok"}) == 200


def _token_app_with_secret(secret: str):
    """同 _token_app, 但配了 internal_secret → loopback 豁免额外要求 X-Internal-Call 信物。"""
    async def embed(_req):
        return JSONResponse({"vec": [1.0]})

    app = Starlette(routes=[Route("/embed", embed, methods=["POST"])])
    auth = TokenAuthenticator({token_hash("good-tok"): {"user_id": "svc", "org_id": "acme"}})
    app.add_middleware(AuthMiddleware, authenticator=auth, public_paths={"/healthz"},
                       internal_secret=secret, loopback_exempt_paths={"/embed", "/rerank"})
    return app


def test_embed_loopback_with_secret_requires_internal_call():
    """配了 internal_secret: loopback + 带对的 X-Internal-Call → 放行(内部调用方)。"""
    app = _token_app_with_secret("s3cr3t")
    assert _run(app, "/embed", client=("127.0.0.1", 5000),
                headers={"X-Internal-Call": "s3cr3t"}) == 200


def test_embed_loopback_with_secret_missing_or_wrong_call_rejected():
    """配了 secret 但 loopback 请求不带 / 带错信物(= 同机反代转发的远程白嫖)→ 落正常鉴权 401。"""
    app = _token_app_with_secret("s3cr3t")
    assert _run(app, "/embed", client=("127.0.0.1", 5000)) == 401            # 不带
    assert _run(app, "/embed", client=("127.0.0.1", 5000),
                headers={"X-Internal-Call": "wrong"}) == 401                  # 带错
    # 但带对的 secret 之外, 合法 Bearer token 仍走正常鉴权放行(反代后的合法远程用户)
    assert _run(app, "/embed", client=("127.0.0.1", 5000),
                headers={"Authorization": "Bearer good-tok"}) == 200


def test_client_is_loopback_helper():
    assert _client_is_loopback({"client": ("127.0.0.1", 1)}) is True
    assert _client_is_loopback({"client": ("127.0.0.5", 1)}) is True
    assert _client_is_loopback({"client": ("::1", 1)}) is True
    assert _client_is_loopback({"client": ("10.0.0.1", 1)}) is False
    assert _client_is_loopback({"client": None}) is False
    assert _client_is_loopback({}) is False
