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
from codev_platform.core.errors import ErrorCode

DEFAULT_WEBHOOK_PORT = 18099
# 8MB 请求体上限, 防超大 payload 拖垮 reindex 队列 / OOM。
# 1MB 太紧: Gitea push payload 内联整段 commit 列表 + 文件清单, 多文件/大 commit 的
# 推送实测 >1MB (2026-06-02 webhook.log 全是 Content-Length=1048577 的 413 拒绝, 即
# 1MB+1 字节), 导致所有 push 被拒、reindex 从不触发。放宽到 8MB 覆盖正常推送。
_MAX_BODY = 8 * 1024 * 1024


def _log_path() -> Path:
    # 落 data_root/logs (非 import 包目录: wheel/只读安装也可写, 见 core.paths.logs_dir)
    from codev_platform.core.paths import logs_dir
    return logs_dir() / "webhook.log"


def _log(msg: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with _log_path().open("a", encoding="utf-8") as f:
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
    """改动文件 → 自动 reindex scope (复用 ops/reindex 分类 + 退役过滤, 单一真值源)。

    A3: cross_link 已退役不自动重建 —— classify_scopes 现已不再产 cross_link scope
    (reindex_patterns 删了该 key), 故无需再过滤; webhook 与本地 hook 同源。
    """
    from codev_platform.ops import _common as C
    from codev_platform.ops.reindex import classify_scopes
    pats = C.reindex_patterns(C.meta_health(pid))
    return list(classify_scopes(changed, pats))


def webhook_port(cfg: dict | None = None) -> int:
    return int(_cfg_get(cfg if cfg is not None else load_config(), "webhook.port") or DEFAULT_WEBHOOK_PORT)


def build_app(middleware=None):
    """构建 Starlette app (路由 + handler), 与 serve 解耦 —— 便于 TestClient 测 413/验签等分支。

    webhook 无 gateway 鉴权 (验签走 provider.verify), 但可叠加限流中间件 (run_http 注入)。
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from codev_platform.webhook import providers
    from codev_platform.reindex import open_default_queue

    async def handle(request):
        name = request.path_params["provider"]
        prov = providers.get_provider(name)
        if prov is None:
            return JSONResponse({"error": f"unknown provider '{name}'", "code": ErrorCode.INVALID_PARAMS.value}, status_code=404)
        # 请求体大小上限 (fail-closed): 先看 Content-Length 头早拒; 无头时读 body 后再校验长度。
        clen = request.headers.get("content-length")
        if clen is not None:
            try:
                if int(clen) > _MAX_BODY:
                    _log(f"[{name}] 请求体 Content-Length={clen} 超上限 {_MAX_BODY}, 拒绝")
                    return JSONResponse({"error": "payload too large", "code": ErrorCode.INVALID_PARAMS.value}, status_code=413)
            except ValueError:
                return JSONResponse({"error": "bad content-length", "code": ErrorCode.INVALID_PARAMS.value}, status_code=400)
        body = await request.body()
        if len(body) > _MAX_BODY:
            _log(f"[{name}] 请求体 {len(body)} 字节超上限 {_MAX_BODY}, 拒绝")
            return JSONResponse({"error": "payload too large", "code": ErrorCode.INVALID_PARAMS.value}, status_code=413)
        cfg = load_config()
        secret = str(_cfg_get(cfg, "webhook.secret") or "")
        allow_insecure = bool(_cfg_get(cfg, "webhook.allow_insecure") or False)
        if not secret:
            if not allow_insecure:
                _log(f"[{name}] webhook.secret 未配置, fail-closed 拒绝; 本机信任可设 webhook.allow_insecure=true")
                return JSONResponse({"error": "webhook secret not configured", "code": ErrorCode.DEPENDENCY_MISSING.value}, status_code=401)
            _log(f"[{name}] webhook.secret 未配置, 但 webhook.allow_insecure=true, 跳过验签放行 (仅限本机信任)")
        elif not prov.verify(request.headers, body, secret):
            _log(f"[{name}] 验签失败 (检查 webhook.secret 与 VCS 配置一致)")
            return JSONResponse({"error": "invalid signature", "code": ErrorCode.ACCESS_DENIED.value}, status_code=401)
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "bad json", "code": ErrorCode.INVALID_PARAMS.value}, status_code=400)
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
        # 代码改动 (codegraph scope) → 顺带刷统一图谱 ingest + 代码向量 lane (与本地 hook 同源)。
        # append 在 codegraph 之后入队 → 串行 worker 保证 ingest/code_vec 读到新鲜 codegraph.db。
        if "codegraph" in scopes:
            if "ingest" not in scopes:
                scopes.append("ingest")
            if "code_vec" not in scopes:
                scopes.append("code_vec")
        q = open_default_queue()
        for kind in scopes:
            q.enqueue(pid, kind)
        _log(f"[{name}] {event.repo} -> {pid} enqueue {scopes} ({len(event.changed_files)} files changed)")
        return JSONResponse({"ok": True, "project_id": pid, "enqueued": scopes})

    async def healthz(_request):
        # PUBLIC 存活探针: 仅最小信息 (审计 #4 — 与三套 MCP 对齐, 不在存活面暴露
        # 已配 provider 清单)。provider 清单移到 /platform/status。
        return JSONResponse({"status": "ok", "service": "webhook"})

    async def platform_status(_request):
        # 详情面: 报已注册 provider 清单。webhook 无 gateway 鉴权中间件 (验签走
        # provider.verify), 故此路径与 /healthz 一样可公开访问, 仅作清单分离用。
        return JSONResponse({"status": "ok", "service": "webhook", "providers": list(providers.names())})

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/health", healthz, methods=["GET"]),  # backward-compat alias (最小)
            Route("/platform/status", platform_status, methods=["GET"]),
            Route("/{provider}", handle, methods=["POST"]),
        ],
        middleware=middleware or [],
    )


async def run_http(port: int | None = None) -> None:
    import uvicorn

    from codev_platform.webhook import providers
    from codev_platform.gateway import maybe_rate_limit_middleware

    if port is None:
        port = webhook_port()
    # webhook 无身份 (无 Auth 中间件) → 限流按 client IP (default_key_from_scope 自动回退); dev 默认关
    _mw = []
    _rl = maybe_rate_limit_middleware(load_config())
    if _rl is not None:
        _mw.append(_rl)
    app = build_app(middleware=_mw)
    _log(f"[http] webhook receiver starting on 127.0.0.1:{port} (providers: {', '.join(providers.names())})")
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    await uvicorn.Server(config).serve()


if __name__ == "__main__":
    import asyncio
    p = None
    if "--port" in sys.argv:
        p = int(sys.argv[sys.argv.index("--port") + 1])
    asyncio.run(run_http(p))
