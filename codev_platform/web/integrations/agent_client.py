"""agent 后端代理客户端 (B1) —— web 前门把已认证身份代理到 codev-agent 的 chat/memory。

web 作鉴权前门, 复用成熟的 agent 子系统 (ChatService / memory_store), 绝不重写其业务逻辑:
本客户端把 web 已认证 Identity 签成 X-Identity 信物 (core.service_identity), POST/GET 到
agent 内网端点; agent 侧验签后采信。下游不可达 / 超时 → PlatformError(UPSTREAM_UNAVAILABLE)
(对齐 codegraph_client 的异常→PlatformError 范式)。

HTTP 走 stdlib urllib (与 platform_status / health 一致, 零额外依赖); 短超时, 不吞错。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from codev_platform.core.config import get as cfg_get
from codev_platform.core.errors import ErrorCode, PlatformError
from codev_platform.core.service_identity import sign_identity

_DEFAULT_BASE_URL = "http://127.0.0.1:8848"
_DEFAULT_TIMEOUT = 30.0


def _claims(ident) -> dict:
    """gateway.auth.Identity → 内部令牌 claims (duck-typed, 不硬依赖 Identity 类)。"""
    projects = getattr(ident, "projects", ()) or ()
    return {
        "user_id": getattr(ident, "user_id", "unknown"),
        "org_id": getattr(ident, "org_id", "default"),
        "projects": sorted(projects),
        "all_projects": bool(getattr(ident, "all_projects", False)),
    }


class AgentClient:
    """per-call 轻量代理。无状态 (无连接池): 每请求签新令牌 + 短连接, 多会话天然并发安全。"""

    def __init__(self, cfg: dict) -> None:
        self._base_url = str(cfg_get(cfg, "agent.base_url", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL).rstrip("/")
        self._secret = cfg_get(cfg, "agent.internal_secret", "") or ""
        self._timeout = float(cfg_get(cfg, "agent.timeout_sec", _DEFAULT_TIMEOUT) or _DEFAULT_TIMEOUT)

    def chat(self, ident, body: dict) -> dict:
        return self._request("POST", "/chat", ident, body=body)

    def memory_write(self, ident, body: dict) -> dict:
        return self._request("POST", "/memory", ident, body=body)

    def memory_list(self, ident, params: dict) -> dict:
        return self._request("GET", "/memory", ident, params=params)

    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, ident, *,
                 body: dict | None = None, params: dict | None = None) -> dict:
        if not self._secret:
            raise PlatformError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "agent 代理未配置 internal_secret",
                detail="set config agent.internal_secret",
            )
        url = self._base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        token = sign_identity(_claims(ident), self._secret)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Identity": token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:  # 下游已返状态 → 透传其 body + 码
            raise PlatformError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "agent 后端返回错误",
                detail=f"{exc.code}: {exc.reason}",
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # 不可达 / 超时
            raise PlatformError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "agent 后端不可达",
                detail=str(exc),
            ) from exc
