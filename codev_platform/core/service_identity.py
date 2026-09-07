"""服务间身份令牌 (B1 共享身份基建) —— web 前门 → agent 后端的内部传身份信物。

web 已认证身份后, 调 agent 内网端点时签一个短时效 HMAC 令牌放 header 'X-Identity';
agent 侧用同一 internal_secret 验签通过即采信, 构造 gateway.auth.Identity 写入 request.state。
不是对外鉴权 (那是 gateway.auth), 是**两个本地进程间**互信的轻量信物。

token 形如 b64url(json claims) + "." + b64url(hmac_sha256(payload, secret));
claims 含 user_id / org_id / projects / all_projects / exp(epoch 秒)。验签或过期失败返 None。
叶子模块: 只依赖 stdlib (hmac/hashlib/base64/json/time), 不 import web / agent。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign_identity(claims: dict, secret: str, ttl_sec: int = 120) -> str:
    """签发身份令牌: 在 claims 上盖 exp=now+ttl, 序列化后接 HMAC-SHA256 签名。"""
    if not secret:
        raise ValueError("service identity secret 不能为空")
    payload = dict(claims)
    payload["exp"] = int(time.time()) + int(ttl_sec)
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_identity(token: str, secret: str) -> dict | None:
    """验签 + 过期校验。任一失败(格式坏/签名不符/已过期)返 None, 绝不抛 (调用方按 None 兜底)。"""
    if not token or not secret:
        return None
    try:
        body, sig = token.split(".", 1)
        expected = _b64url(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):  # 常量时间比对, 防时序侧信道
            return None
        claims = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None  # 缺 exp / 已过期 → 拒绝 (安全默认)
    return claims
