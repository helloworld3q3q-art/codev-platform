"""认证策略(可插拔)—— credential → Identity。

接口 Authenticator;两个实现:
  - PassthroughAuthenticator:信任 X-User-Id / X-Org-Id 头(单人/开发期,不验签)。
  - TokenAuthenticator:验 Authorization: Bearer <token> → 映射到 (org,user)(M6,token 走 config/PG)。
loop / 路由只依赖 Identity + 抽象,换鉴权方式零改(同 agent provider 的策略接口思路)。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from codev_platform.core import identity as _identity
from codev_platform.core.config import get as _cfg_get
from codev_platform.core.project_id import ProjectIdError, validate as _validate_pid

_log = logging.getLogger("codev_platform.gateway")


def token_hash(token: str) -> str:
    """token → sha256 十六进制。config 只存 hash,明文 token 不落盘/不进 git。
    生成:`python -c "import hashlib;print(hashlib.sha256(b'你的token').hexdigest())"`。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Unauthorized(Exception):
    """认证失败(缺/坏 token、身份非法、token 过期)。中间件转 401。"""


def token_expired(meta: Mapping[str, Any], now_epoch: float) -> bool:
    """token 是否过期(纯函数, now 入参保可测)。

    判定:
      - meta 无 "expires_at" → 永不过期(返回 False)。
      - expires_at 为 epoch 秒(int/float)或可解析 ISO 字符串, 且 < now → 过期(True)。
      - 解析失败(坏值) → 按**已过期**(True), 安全默认拒绝(不让脏配置变成永久有效)。
    """
    raw = meta.get("expires_at")
    if raw is None or raw == "":
        return False  # 无 expires_at = 永久 token
    try:
        if isinstance(raw, bool):
            raise TypeError("bool 不是合法 expires_at")
        if isinstance(raw, (int, float)):
            exp = float(raw)
        else:
            from datetime import datetime
            s = str(raw).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            exp = datetime.fromisoformat(s).timestamp()
    except Exception:  # noqa: BLE001 坏值按已过期(安全默认)
        return True
    return exp < now_epoch


@dataclass(frozen=True)
class Identity:
    """请求级身份(认证结果)。org 是租户根,user 是主体,via 标认证方式(审计用)。

    projects / all_projects 是 token 模式的 project 白名单(ACL 闸2 真值, 见 core/acl.py):
    all_projects=True 时无视 projects 放行全部;否则仅 project_id ∈ projects 放行。
    passthrough 模式 dev 信任, all_projects=True。
    """
    user_id: str
    org_id: str
    via: str  # passthrough | token
    projects: frozenset[str] = frozenset()
    all_projects: bool = False


@runtime_checkable
class Authenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str]) -> Identity:
        ...


def _header(headers: Mapping[str, str], name: str) -> str | None:
    # headers 大小写不敏感(Starlette Headers 已是);兜底手动找
    if hasattr(headers, "get"):
        v = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper())
        if v:
            return v
    return None


def _bearer(headers: Mapping[str, str]) -> str | None:
    raw = _header(headers, "Authorization")
    if raw and raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return None


class PassthroughAuthenticator:
    """单人/开发期:信任明文头解析身份,不验签。是"身份解析+上下文",不是真鉴权。"""

    def authenticate(self, headers: Mapping[str, str]) -> Identity:
        try:
            user_id = _identity.resolve_from_request(headers)
            org_id = _identity.resolve_org_from_request(headers)
        except ValueError as exc:
            raise Unauthorized(f"非法身份头: {exc}") from exc
        # dev 信任: passthrough 不验签, 给全 project 放行(ACL 走 advisory allow)。
        return Identity(user_id=user_id, org_id=org_id, via="passthrough", all_projects=True)


class TokenAuthenticator:
    """验 Bearer token → (org,user)。**认证加密**:config/PG 只存 token 的 sha256 hash
    (明文 token 不落盘),每请求 hash 入参 + `hmac.compare_digest` 常量时间比对(防时序攻击)。

    token_hashes 形如 {<sha256hex>: {"user_id":..,"org_id":..}}。真 token 表应走 PG
    (codev_platform_memory),config 仅放本机调用方自己的 key hash。
    传输加密(HTTPS/TLS)是部署层:远程平台 platform.url 用 https,TLS 在反代/uvicorn 终结。"""

    def __init__(self, token_hashes: Mapping[str, Mapping[str, Any]]) -> None:
        self._by_hash = {str(k): dict(v) for k, v in (token_hashes or {}).items()}

    def authenticate(self, headers: Mapping[str, str]) -> Identity:
        tok = _bearer(headers)
        if not tok:
            raise Unauthorized("缺少 Authorization: Bearer <token>")
        presented = token_hash(tok)
        ident = None
        # 遍历 + 常量时间比对:不因命中/字符差异泄漏时序;hash 本身已使明文不可逆。
        # 不 break —— 命中后仍走完全表, 使命中/未命中耗时一致(消除"提前返回"时序侧信道)。
        for h, meta in self._by_hash.items():
            if hmac.compare_digest(h, presented):
                ident = meta
        if ident is None:
            raise Unauthorized("无效 token")
        # 过期判定放命中后(不破坏遍历的常量时间特性: 过期 check 不依赖输入字符差异)。
        if token_expired(ident, time.time()):
            raise Unauthorized("token 已过期")
        # project 白名单解析(ACL 闸2 真值): "*"/["*"]=全部; list/tuple=显式白名单;
        # 缺省/其它=无权(安全默认, 不给空 token 越权访问所有项目)。
        raw = ident.get("projects")
        if raw == "*" or raw == ["*"]:
            all_projects = True
            projects: frozenset[str] = frozenset()
        elif isinstance(raw, (list, tuple)):
            # 逐个过 project_id 格式校验: 合法保留, 非法跳过 + warning。
            # 不 raise —— 一条脏配置不应炸掉整个认证, 只剔除该项 (安全侧: 剔除即少放行)。
            valid: set[str] = set()
            for p in raw:
                try:
                    valid.add(_validate_pid(p))
                except ProjectIdError as exc:
                    _log.warning(
                        "[gateway] token 白名单含非法 project_id %r, 已跳过: %s", p, exc,
                    )
            projects = frozenset(valid)
            all_projects = False
        else:
            projects = frozenset()
            all_projects = False
        return Identity(
            user_id=str(ident.get("user_id") or "unknown"),
            org_id=str(ident.get("org_id") or "default"),
            via="token",
            projects=projects,
            all_projects=all_projects,
        )


def build_authenticator(cfg: dict | None = None) -> Authenticator:
    """按 config.gateway.auth_mode 选认证器(默认 passthrough)。换模式零改上层。"""
    mode = _cfg_get(cfg or {}, "gateway.auth_mode", "passthrough") if cfg is not None else "passthrough"
    if mode == "token":
        return TokenAuthenticator(_cfg_get(cfg or {}, "gateway.tokens", {}) or {})
    return PassthroughAuthenticator()


def _is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in {"127.0.0.1", "::1", "localhost", ""}


def deploy_policy_error(cfg: dict | None, host: str) -> str | None:
    """prod 部署的认证 fail-fast 策略(纯函数, 可测)。

    返回错误串 = 必须拒绝启动; None = 放行。
    判定(任一命中即拒绝):
      - deployment.mode == "prod" 且 gateway.auth_mode != "token"
      - platform.url 非 localhost(对外暴露) 且 gateway.auth_mode != "token"
    warn_if_insecure 保留作 dev 软告警, 这里是 prod 硬拒。
    """
    c = cfg or {}
    auth_mode = _cfg_get(c, "gateway.auth_mode", "passthrough")
    if auth_mode == "token":
        return None
    mode = _cfg_get(c, "deployment.mode", "dev")
    if mode == "prod":
        return ("deployment.mode=prod 但 gateway.auth_mode=%s —— prod 模式必须 auth_mode=token "
                "(见 config.example.json)。" % auth_mode)
    url = _cfg_get(c, "platform.url", None)
    if url and not _url_is_loopback(str(url)):
        return ("platform.url=%s 指向远程(非 localhost) 但 gateway.auth_mode=%s —— "
                "对外暴露必须 auth_mode=token (见 config.example.json)。" % (url, auth_mode))
    return None


def _url_is_loopback(url: str) -> bool:
    """从 platform.url 抽 host 判断是否 loopback。容错: 解析失败按非 loopback(更安全, 倾向拒绝)。"""
    from urllib.parse import urlparse
    try:
        host = urlparse(url if "://" in url else "//" + url).hostname or ""
    except Exception:  # noqa: BLE001
        return False
    return _is_loopback(host)


def warn_if_insecure(authenticator: Authenticator, host: str) -> None:
    """passthrough 绑非 loopback = 未认证对外开放 → 启动期 loud WARN(secure-by-default 兜底)。

    passthrough 不验签, 任何请求落 local/default。绑 127.0.0.1 仅本机可达可接受;一旦绑
    0.0.0.0 / 实 IP 暴露到网络, 必须切 token 模式。挂载端点时调一次。"""
    if isinstance(authenticator, PassthroughAuthenticator) and not _is_loopback(host):
        _log.warning(
            "[gateway] auth_mode=passthrough 绑非 loopback host=%s —— 未认证对外开放! "
            "网络暴露请切 config.gateway.auth_mode=token(见 config.example.json)。", host,
        )
