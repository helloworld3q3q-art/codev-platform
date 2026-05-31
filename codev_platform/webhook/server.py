"""webhook 接收器 —— 收 VCS push 事件 → 入 reindex 写队列 (push 即触发 reindex)。

  POST /<provider>  (gitea / gitlab / ...)
    → providers.get_provider → verify(secret) → parse → 中性 PushEvent
    → repo → project_id (config.projects.<pid>.webhook_repo)
    → classify_scopes (复用 ops/reindex, 与本地 hook 同源)
    → reindex.open_default_queue().enqueue —— 之后 codev-reindex worker 串行消费

编排层: 不碰验签/解析 (providers) 也不碰 reindex 怎么跑 (reindex worker), 只串起来。
webhook 自带验签 (provider.verify), 不挂 MCP 的 gateway token 鉴权 (两套独立)。
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

from codev_platform.core.config import get as _cfg_get, load_config

_LOG_FILE = Path(__file__).resolve().parent / "webhook.log"
DEFAULT_WEBHOOK_PORT = 18099


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, file=sys.stderr, flush=True)


def _project_for_repo(cfg: dict, repo: str) -> str | None:
    """repo (owner/name) → project_id, 按 config.projects.<pid>.webhook_repo 匹配。"""
    projects = _cfg_get(cfg, "projects") or {}
    for pid, pc in projects.items():
        if isinstance(pc, dict) and str(pc.get("webhook_repo") or "").strip() == repo:
            return pid
    return None


def _scopes_for(pid: str, changed: list[str]) -> list[str]:
    """改动文件 → reindex scope (复用 ops/reindex 的分类, 单一真值源)。"""
    from codev_platform.ops import _common as C
    from codev_platform.ops.reindex import classify_scopes
    pats = C.reindex_patterns(C.meta_health(pid))
    return list(classify_scopes(changed, pats).keys())


def webhook_port(cfg: dict | None = None) -> int:
    return int(_cfg_get(cfg if cfg is not None else load_config(), "webhook.port") or DEFAULT_WEBHOOK_PORT)


async def run_http(port: int | None = None) -> None:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    import uvicorn

    from codev_platform.webhook import providers
    from codev_platform.reindex import open_default_queue

    if port is None:
        port = webhook_port()

    async def handle(request):
        name = request.path_params["provider"]
        prov = providers.get_provider(name)
        if prov is None:
            return JSONResponse({"error": f"unknown provider '{name}'"}, status_code=404)
        body = await request.body()
        cfg = load_config()
        secret = str(_cfg_get(cfg, "webhook.secret") or "")
        allow_insecure = bool(_cfg_get(cfg, "webhook.allow_insecure") or False)
        if not secret:
            if not allow_insecure:
                _log(f"[{name}] webhook.secret 未配置, fail-closed 拒绝; 本机信任可设 webhook.allow_insecure=true")
                return JSONResponse({"error": "webhook secret not configured"}, status_code=401)
            _log(f"[{name}] webhook.secret 未配置, 但 webhook.allow_insecure=true, 跳过验签放行 (仅限本机信任)")
        elif not prov.verify(request.headers, body, secret):
            _log(f"[{name}] 验签失败 (检查 webhook.secret 与 VCS 配置一致)")
            return JSONResponse({"error": "invalid signature"}, status_code=401)
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad json"}, status_code=400)
        event = prov.parse(request.headers, payload)
        if event is None:
            _log(f"[{name}] 非 push 事件, 跳过")
            return JSONResponse({"ok": True, "skipped": "non-push event"})
        pid = _project_for_repo(cfg, event.repo)
        if pid is None:
            _log(f"[{name}] repo '{event.repo}' 未映射 project (配 projects.<pid>.webhook_repo)")
            return JSONResponse({"ok": True, "skipped": f"unmapped repo {event.repo}"})
        scopes = _scopes_for(pid, event.changed_files)
        if not scopes:
            _log(f"[{name}] {event.repo} -> {pid}: {len(event.changed_files)} 文件改动但无 reindex scope 命中, 跳过")
            return JSONResponse({"ok": True, "project_id": pid, "skipped": "no scope match"})
        q = open_default_queue()
        for kind in scopes:
            q.enqueue(pid, kind)
        _log(f"[{name}] {event.repo} -> {pid} enqueue {scopes} ({len(event.changed_files)} files changed)")
        return JSONResponse({"ok": True, "project_id": pid, "enqueued": scopes})

    async def health(_request):
        return JSONResponse({"status": "ok", "service": "webhook", "providers": list(providers.names())})

    app = Starlette(routes=[
        Route("/health", health, methods=["GET"]),
        Route("/{provider}", handle, methods=["POST"]),
    ])
    _log(f"[http] webhook receiver starting on 127.0.0.1:{port} (providers: {', '.join(providers.names())})")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    import asyncio
    p = None
    if "--port" in sys.argv:
        p = int(sys.argv[sys.argv.index("--port") + 1])
    asyncio.run(run_http(p))
