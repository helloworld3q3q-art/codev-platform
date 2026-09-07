"""RSA 登录口令传输加密 (方案 B) —— 启动期生成内存密钥对。

前端用公钥 (JSEncrypt, PKCS1v1.5) 加密口令再传, 后端私钥解密 → 请求体不出现明文口令 (合规/纵深防御)。
真正防 MITM 靠 TLS (方案 A); 本层是 defense-in-depth, 不能替代 TLS。

密钥进程内 ephemeral: 每次重启换一对, 前端每次登录前拉最新公钥; 私钥不落盘 (无泄漏面)。
依赖 cryptography (venv 已有); 缺失时 get_keypair() 返回 None → 登录层 fallback 收明文 (向后兼容)。
"""
from __future__ import annotations

import base64
import logging

_log = logging.getLogger("codev_platform.web")


class RsaKeypair:
    """2048-bit RSA 内存密钥对。public_pem 给前端; decrypt_b64 解前端密文。"""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._pub_pem = (
            self._key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )

    @property
    def public_pem(self) -> str:
        return self._pub_pem

    def decrypt_b64(self, ciphertext_b64: str) -> str | None:
        """base64(RSA/PKCS1v1.5 密文) → 明文; 非密文/坏数据 → None (调用方 fallback 收原值)。"""
        from cryptography.hazmat.primitives.asymmetric import padding

        try:
            raw = base64.b64decode(ciphertext_b64, validate=True)
            return self._key.decrypt(raw, padding.PKCS1v15()).decode("utf-8")
        except Exception:  # noqa: BLE001 — 解密失败按"非加密输入"处理, 由上层 fallback
            return None


_keypair: RsaKeypair | None = None
_tried = False


def get_keypair() -> RsaKeypair | None:
    """惰性单例。cryptography 缺失 / 生成失败 → None (登录层降级收明文)。"""
    global _keypair, _tried
    if _keypair is None and not _tried:
        _tried = True
        try:
            _keypair = RsaKeypair()
        except Exception as exc:  # noqa: BLE001
            _log.warning("[web] RSA keypair 不可用 (%r); 口令加密降级为明文兼容", exc)
            _keypair = None
    return _keypair
