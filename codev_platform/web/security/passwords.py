"""密码哈希 —— stdlib pbkdf2 (无外部 bcrypt 依赖, 保持 pyproject 轻量)。

格式: pbkdf2_sha256$<iters>$<salt_b64>$<dk_b64>。明文绝不落库 (security.md)。
verify 用 hmac.compare_digest 常量时间比对 (防时序)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

_ALGO = "pbkdf2_sha256"
_ITERS = 200_000
_SALT_BYTES = 16


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password 不能为空")
    salt = os.urandom(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERS)
    return f"{_ALGO}${_ITERS}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """校验明文 vs 存储 hash。任何解析异常 / 不匹配 → False (安全默认)。"""
    try:
        algo, iters_s, salt_b64, dk_b64 = stored.split("$")
        if algo != _ALGO:
            return False
        iters = int(iters_s)
        expected = _ub64(dk_b64)
        actual = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"), _ub64(salt_b64), iters)
    except Exception:  # noqa: BLE001 — 坏 hash 按不匹配
        return False
    return hmac.compare_digest(actual, expected)
