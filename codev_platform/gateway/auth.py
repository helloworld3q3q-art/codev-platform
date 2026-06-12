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
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from collections.abc import Mapping

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


def identity_from_internal_claims(claims: Mapping[str, Any]) -> Identity:
    """已验签的服务间 X-Identity claims → Identity (via='internal')。

    claims 来自 core.service_identity.verify_identity (web 前门签发, 已过期/验签校验)。
    project 白名单解析复用 token 模式语义: all_projects=True 无视 projects; 否则按 list 过滤。
    """
    raw = claims.get("projects")
    projects: frozenset[str] = frozenset(str(p) for p in raw) if isinstance(raw, (list, tuple)) else frozenset()
    return Identity(
        user_id=str(claims.get("user_id") or "unknown"),
        org_id=str(claims.get("org_id") or "default"),
        via="internal",
        projects=projects,
        all_projects=bool(claims.get("all_projects", False)),
    )


@runtime_checkable
class Authenticator(Protocol):
    def authenticate(self, headers: Mapping[str, str], query: str = "") -> Identity:
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


def _query_token(query: str) -> str | None:
    """从 query string 取 `?token=`(SSE/MCP 客户端无法设 Authorization header 时的兜底)。

    **header(Bearer)优先且更安全**(不进 URL/access log);query token 仅给"只能设 SSE URL、
    不能设 header"的 MCP 客户端用。部署侧建议: 远程走 HTTPS(TLS 终结于反代)+ 关 uvicorn access
    log 或脱敏, 避免 ?token= 落日志。LAN/单机 sim 可接受。
    """
    if not query:
        return None
    from urllib.parse import parse_qs
    vals = parse_qs(query).get("token")
    if vals and vals[0].strip():
        return vals[0].strip()
    return None


class PassthroughAuthenticator:
    """单人/开发期:信任明文头解析身份,不验签。是"身份解析+上下文",不是真鉴权。"""

    def authenticate(self, headers: Mapping[str, str], query: str = "") -> Identity:
        try:
            user_id = _identity.resolve_from_request(headers)
            org_id = _identity.resolve_org_from_request(headers)
        except ValueError as exc:
            raise Unauthorized(f"非法身份头: {exc}") from exc
        # dev 信任: passthrough 不验签, 给全 project 放行(ACL 走 advisory allow)。
        return Identity(user_id=user_id, org_id=org_id, via="passthrough", all_projects=True)


def _meta_to_identity(meta: Mapping[str, Any]) -> Identity:
    """token meta(含 user_id/org_id/projects)→ Identity(via='token')。

    project 白名单解析(ACL 闸2 真值): "*"/["*"]=全部; list/tuple=显式白名单;
    缺省/其它=无权(安全默认, 不给空 token 越权访问所有项目)。非法 pid 剔除不 raise
    (一条脏配置不该炸认证, 剔除即少放行)。config / PG 两来源共用此组装, 语义一致。
    """
    raw = meta.get("projects")
    if raw == "*" or raw == ["*"]:
        all_projects = True
        projects: frozenset[str] = frozenset()
    elif isinstance(raw, (list, tuple)):
        valid: set[str] = set()
        for p in raw:
            try:
                valid.add(_validate_pid(p))
            except ProjectIdError as exc:
                _log.warning("[gateway] token 白名单含非法 project_id %r, 已跳过: %s", p, exc)
        projects = frozenset(valid)
        all_projects = False
    else:
        projects = frozenset()
        all_projects = False
    return Identity(
        user_id=str(meta.get("user_id") or "unknown"),
        org_id=str(meta.get("org_id") or "default"),
        via="token", projects=projects, all_projects=all_projects,
    )


@runtime_checkable
class TokenResolver(Protocol):
    """token 查找策略: presented_hash(Bearer 的 sha256)→ token meta 或 None。

    实现负责"hash → meta"的来源差异(config 静态字典 / PG 实时查 + 用户状态校验);
    TokenAuthenticator 只依赖本接口, 组装 Identity + 过期判定与来源无关(策略模式, 零分支)。
    """
    def resolve(self, presented_hash: str) -> Mapping[str, Any] | None:
        ...


class MappingTokenResolver:
    """config `gateway.tokens` 字典来源: 常量时间遍历比对(dev fallback / bootstrap admin)。"""

    def __init__(self, token_hashes: Mapping[str, Mapping[str, Any]]) -> None:
        self._by_hash = {str(k): dict(v) for k, v in (token_hashes or {}).items()}

    def resolve(self, presented_hash: str) -> Mapping[str, Any] | None:
        ident = None
        # 不 break —— 命中后仍走完全表, 使命中/未命中耗时一致(消除"提前返回"时序侧信道)。
        for h, meta in self._by_hash.items():
            if hmac.compare_digest(h, presented_hash):
                ident = meta
        return ident


class CompositeTokenResolver:
    """按序尝试多个 resolver, 首个命中即返回。PG 优先 + config 兜底(迁移期 bootstrap token 仍可用)。"""

    def __init__(self, resolvers: list[TokenResolver]) -> None:
        self._resolvers = [r for r in resolvers if r is not None]

    def resolve(self, presented_hash: str) -> Mapping[str, Any] | None:
        for r in self._resolvers:
            meta = r.resolve(presented_hash)
            if meta is not None:
                return meta
        return None


class PgTokenResolver:
    """PG `agent_tokens` 来源: lookup 内 join users.status 实时校验(禁用用户 token 即失效)。

    DB 唯一索引按 hash 直查(hash 单向, 不泄漏明文), 无需遍历常量时间比对。store 异常 fail-closed
    (返回 None = 认证失败), 不把 DB 抖动变成放行。
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def resolve(self, presented_hash: str) -> Mapping[str, Any] | None:
        try:
            return self._store.lookup(presented_hash)
        except Exception as exc:  # noqa: BLE001 — DB 抖动 fail-closed(拒绝优于误放行)
            _log.warning("[gateway] PG token lookup 失败, 拒绝该 token: %s", exc)
            return None


class TokenAuthenticator:
    """验 Bearer token → Identity。**认证加密**: 只存/比对 token 的 sha256 hash(明文不落盘)。

    token 来源经 `TokenResolver` 策略注入(config 字典 / PG 实时 / 组合), 本类只做: 取 token →
    hash → resolver.resolve → 过期判定 → 组装 Identity(来源无关)。向后兼容: 传 dict 自动包成
    MappingTokenResolver(旧调用 `TokenAuthenticator({...})` 不变)。
    传输加密(HTTPS/TLS)是部署层: 远程平台 platform.url 用 https, TLS 在反代/uvicorn 终结。
    """

    def __init__(self, tokens_or_resolver: Mapping[str, Mapping[str, Any]] | TokenResolver) -> None:
        # 有 .resolve = 已是策略对象直接用; 否则按 config 字典包成 MappingTokenResolver(向后兼容)。
        if hasattr(tokens_or_resolver, "resolve"):
            self._resolver: TokenResolver = tokens_or_resolver  # type: ignore[assignment]
        else:
            self._resolver = MappingTokenResolver(tokens_or_resolver or {})  # type: ignore[arg-type]

    def authenticate(self, headers: Mapping[str, str], query: str = "") -> Identity:
        tok = _bearer(headers) or _query_token(query)   # header 优先, SSE 无 header 时 ?token= 兜底
        if not tok:
            raise Unauthorized("缺少 Authorization: Bearer <token> 或 ?token=<token>")
        meta = self._resolver.resolve(token_hash(tok))
        if meta is None:
            raise Unauthorized("无效 token")
        if token_expired(meta, time.time()):
            raise Unauthorized("token 已过期")
        return _meta_to_identity(meta)


def _build_token_resolver(cfg: dict) -> TokenResolver:
    """token 模式的 resolver 装配: PG 优先(实时 user-active 校验)+ config 兜底(bootstrap admin)。

    配了 memory.pg_dsn 且 psycopg 可用 → PgTokenResolver 进组合首位(web 管的用户 token 走它,
    禁用即失效); config `gateway.tokens` 始终作兜底(迁移期 / 单机 bootstrap)。两者皆无 = 纯 config。
    PG 不可用(缺 psycopg / DSN 坏)优雅降级到 config-only(不挂服务, 同 deps 范式)。
    """
    config_tokens = _cfg_get(cfg, "gateway.tokens", {}) or {}
    resolvers: list[TokenResolver] = []
    dsn = _cfg_get(cfg, "memory.pg_dsn", None) or os.environ.get("CODEV_PLATFORM_MEMORY_DSN")
    if dsn:
        try:
            from codev_platform.gateway.token_store_pg import PgTokenStore
            read_dsn = _cfg_get(cfg, "memory.pg_dsn_read", None)
            resolvers.append(PgTokenResolver(PgTokenStore(dsn, read_dsn=read_dsn)))
        except Exception as exc:  # noqa: BLE001 — 缺 psycopg / DSN 坏 → 降级 config-only, 不挂服务
            _log.warning("[gateway] PG token store 不可用, 降级 config-only token: %s", exc)
    resolvers.append(MappingTokenResolver(config_tokens))  # config 始终兜底(bootstrap)
    return resolvers[0] if len(resolvers) == 1 else CompositeTokenResolver(resolvers)


def build_authenticator(cfg: dict | None = None) -> Authenticator:
    """按 config.gateway.auth_mode 选认证器(默认 passthrough)。换模式零改上层。"""
    mode = _cfg_get(cfg or {}, "gateway.auth_mode", "passthrough") if cfg is not None else "passthrough"
    if mode == "token":
        return TokenAuthenticator(_build_token_resolver(cfg or {}))
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
        return (f"deployment.mode=prod 但 gateway.auth_mode={auth_mode} —— prod 模式必须 auth_mode=token "
                "(见 config.example.json)。")
    url = _cfg_get(c, "platform.url", None)
    if url and not _url_is_loopback(str(url)):
        return (f"platform.url={url} 指向远程(非 localhost) 但 gateway.auth_mode={auth_mode} —— "
                "对外暴露必须 auth_mode=token (见 config.example.json)。")
    return None


def multi_user_policy_error(cfg: dict | None) -> str | None:
    """多 dev 共用的认证 fail-fast 策略(纯函数, 可测)—— dev-agent-memory P3 护栏。

    单人自己机器的 WSL(loopback、只有自己)passthrough 无人可串, 零风险;但一旦变成"多人共用
    同一台 WSL / 内网服务器"却没切 token, personal 记忆会串号(都落同一 advisory 身份)。这段
    "忘了切配置"的过渡空窗用代码堵: 检测到多 dev 迹象 + 仍 passthrough → 拒绝启动(不靠人记得切)。

    返回错误串 = 必须拒绝启动; None = 放行。判定(auth_mode!=token 且任一命中):
      - gateway.multi_user == true(管理员显式声明多人)
      - len(gateway.tokens) > 1(登记了多个 token = 多主体)
    """
    c = cfg or {}
    auth_mode = _cfg_get(c, "gateway.auth_mode", "passthrough")
    if auth_mode == "token":
        return None
    multi_user = bool(_cfg_get(c, "gateway.multi_user", False))
    tokens = _cfg_get(c, "gateway.tokens", {}) or {}
    n_tokens = len(tokens) if isinstance(tokens, dict) else 0
    if multi_user or n_tokens > 1:
        why = "gateway.multi_user=true" if multi_user else f"检测到 {n_tokens} 个登记 token"
        return (f"{why} 但 gateway.auth_mode={auth_mode} —— 多 dev 共用必须 auth_mode=token "
                "(passthrough 会 personal 串号;见 config.example.json)。")
    return None


def bind_policy_error(cfg: dict | None, host: str) -> str | None:
    """bind host 安全策略(纯函数, 可测)—— warn_if_insecure 的**硬拒升级版**(multi-org P1.2)。

    passthrough 不验签: 绑 loopback 仅本机可达可接受, 一旦绑 0.0.0.0 / 实 IP 暴露到网络 =
    未认证对外开放。原 warn_if_insecure 只 loud WARN(靠人记得切), 多机部署下不够 —— 这里
    升级成启动硬拒(不靠人自觉)。token 模式放行(已验签, 对外暴露安全)。

    返回错误串 = 必须拒绝启动; None = 放行。
    """
    c = cfg or {}
    if _cfg_get(c, "gateway.auth_mode", "passthrough") == "token":
        return None
    if not _is_loopback(host):
        return (f"bind host={host} 非 loopback 但 gateway.auth_mode=passthrough —— "
                "未认证对外开放, 必须 auth_mode=token (见 config.example.json)。")
    return None


def startup_policy_error(cfg: dict | None, host: str) -> str | None:
    """MCP / web 启动统一认证策略闸: 聚合三条 policy_error, 返回**首个**命中(None=放行)。

    单一入口 —— 各 HTTP server 启动调一次即可, 不再各自拼 policy 列表(消重 + 防"漏挂某条")。
    顺序 = 多 dev 串号 > prod/远程暴露 > bind 暴露, 任一命中即拒绝启动。
    """
    for err in (
        multi_user_policy_error(cfg),
        deploy_policy_error(cfg, host),
        bind_policy_error(cfg, host),
    ):
        if err:
            return err
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
