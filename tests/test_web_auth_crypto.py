"""登录口令加密 (方案 B RSA) + TLS 配置 (方案 A) 测试。

RSA 需 cryptography (venv 已有, CI 缺则 skip)。验证: 公钥加密→私钥解密往返、登录收 RSA 密文、
明文 fallback 兼容、public-key 端点、web_tls 配置解析。
"""
from __future__ import annotations

import base64

import pytest

pytest.importorskip("cryptography")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from codev_platform.core.httpkit import build_app  # noqa: E402
from codev_platform.web.config import web_tls  # noqa: E402
from codev_platform.web.domain.accounts import User  # noqa: E402
from codev_platform.web.repositories.account_store import (  # noqa: E402
    get_user_store,
    reset_account_stores,
)
from codev_platform.web.routes import auth as auth_route  # noqa: E402
from codev_platform.web.security.passwords import hash_password  # noqa: E402
from codev_platform.web.security.rsa_keys import get_keypair  # noqa: E402
from codev_platform.web.security.sessions import session_store  # noqa: E402
from codev_platform.web.services.auth_service import AuthService  # noqa: E402

_CFG = {"gateway": {"auth_mode": "passthrough"}, "projects": {}}


@pytest.fixture(autouse=True)
def _clean():
    reset_account_stores()
    session_store.clear()
    yield
    reset_account_stores()
    session_store.clear()


def _encrypt(pem: str, plaintext: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pub = load_pem_public_key(pem.encode("ascii"))
    ct = pub.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return base64.b64encode(ct).decode("ascii")


# ---- RSA 往返 ----

def test_rsa_roundtrip():
    kp = get_keypair()
    assert kp is not None and kp.public_pem.startswith("-----BEGIN PUBLIC KEY-----")
    enc = _encrypt(kp.public_pem, "s3cret-pw")
    assert kp.decrypt_b64(enc) == "s3cret-pw"
    assert kp.decrypt_b64("not-ciphertext") is None  # 非密文 → None


# ---- 登录: 收 RSA 密文 + 明文 fallback ----

def _seed_user():
    get_user_store().create(User(username="u1", password_hash=hash_password("pw123"), org_id="o1"))


def test_login_accepts_rsa_encrypted_password():
    _seed_user()
    kp = get_keypair()
    enc = _encrypt(kp.public_pem, "pw123")
    pair = AuthService().login(username="u1", password=enc)
    assert pair.accessToken and pair.refreshToken


def test_login_still_accepts_plaintext():
    _seed_user()
    pair = AuthService().login(username="u1", password="pw123")  # fallback 明文
    assert pair.accessToken


def test_login_rejects_wrong_encrypted_password():
    from codev_platform.core.errors import PlatformError

    _seed_user()
    kp = get_keypair()
    enc = _encrypt(kp.public_pem, "WRONG")
    with pytest.raises(PlatformError):
        AuthService().login(username="u1", password=enc)


# ---- public-key 端点 ----

def test_public_key_endpoint():
    app = build_app(title="t", routers=[auth_route.router], cfg=_CFG,
                    public_paths=("/health", "/api/v1/auth/public-key"))
    r = TestClient(app).get("/api/v1/auth/public-key")
    assert r.status_code == 200
    assert r.json()["data"]["publicKey"].startswith("-----BEGIN PUBLIC KEY-----")


# ---- 方案 A: TLS 配置解析 ----

def test_web_tls_config():
    assert web_tls({"web": {}}) is None
    assert web_tls({"web": {"tls_cert": "c.pem", "tls_key": "k.pem"}}) == ("c.pem", "k.pem")
    assert web_tls({"web": {"tls_cert": "c.pem"}}) is None  # 缺 key → None
