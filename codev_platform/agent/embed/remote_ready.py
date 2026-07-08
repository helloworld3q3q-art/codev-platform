"""Readiness guard for code_vec remote embedding.

The general remote embedder stays side-effect free for query-time degradation.
This guard is used only by the code_vec write path, where a missing local
platform-docs daemon should be fixed or reported before the expensive index job
starts embedding batches.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

from codev_platform.core.config import get as _cfg_get
from codev_platform.mcp_runtime import spawn_endpoint


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"0", "false", "no", "off"}


def _platform_docs_endpoint(cfg: dict):
    from codev_platform import mcp_serve

    for ep in mcp_serve.iter_endpoints(cfg):
        if ep.name == "platform-docs":
            return ep
    raise RuntimeError("platform-docs endpoint not registered")


def _is_managed_embed_url(cfg: dict, ep) -> bool:
    explicit = _cfg_get(cfg, "memory.embed.url")
    if not explicit:
        return True
    try:
        parsed = urlsplit(str(explicit))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/") or "/"
    return host in {"127.0.0.1", "localhost", "::1"} and port == ep.port and path == "/embed"


def _wait_endpoint(ep, cfg: dict, timeout: float) -> tuple[bool, str]:
    from codev_platform import mcp_serve

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if mcp_serve.probe(ep) == "ok":
            return True, ""
        if time.monotonic() >= deadline:
            facts = mcp_serve.collect_facts(ep, cfg)
            return False, mcp_serve.diagnose_down(ep, **facts)
        time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))


def ensure_code_vec_remote_daemon(cfg: dict) -> dict[str, Any]:
    """Ensure the local platform-docs daemon for code_vec remote embedding.

    No-op cases:
    - `recall.code_vec.ensure_remote_daemon=false`
    - `memory.embed.url` points at an external/custom endpoint
    """
    enabled = _as_bool(_cfg_get(cfg, "recall.code_vec.ensure_remote_daemon"), True)
    if not enabled:
        return {"name": "platform-docs", "action": "disabled", "status": "skipped"}

    from codev_platform import mcp_serve

    ep = _platform_docs_endpoint(cfg)
    if not _is_managed_embed_url(cfg, ep):
        return {"name": ep.name, "action": "external-url", "status": "skipped"}
    if mcp_serve.probe(ep) == "ok":
        return {"name": ep.name, "action": "already-up", "status": "ok"}

    result = spawn_endpoint(ep)
    if result["action"] in {"fail", "skip"}:
        detail = result.get("error") or result.get("note") or result["action"]
        raise RuntimeError(f"code_vec remote embed daemon unavailable: {detail}")

    timeout = float(_cfg_get(cfg, "recall.code_vec.remote_daemon_wait_sec", 90.0))
    ok, reason = _wait_endpoint(ep, cfg, timeout)
    if ok:
        return {**result, "status": "ok"}
    suffix = f": {reason}" if reason else ""
    raise RuntimeError(
        "code_vec remote embed daemon not ready "
        f"after {timeout:.0f}s{suffix}; run `codev-platform serve-mcp start --wait` "
        "or set `recall.code_vec.embed_backend=qwen-local` on a dedicated GPU index node."
    )
