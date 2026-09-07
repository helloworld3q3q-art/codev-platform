"""回滚受保护载荷的 descriptor-safe 文件与身份校验。"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol

from codev_platform.core.runtime_models import canonical_json_bytes
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFilePolicy,
    read_managed_bytes_at,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_rollback_contract import (
    PayloadIdentityKind,
    ProtectedPayloadRef,
    decode_protected_payload_ref,
    encode_protected_payload_ref,
)


_MAX_PROTECTED_PAYLOAD_BYTES = 1_048_576
_HMAC_DOMAIN = b"codev-platform/runtime-protected-payload/hmac/v1\x00"


class ProtectedPayloadVerificationError(RuntimeError):
    """受保护载荷的文件证据或身份校验不满足安全边界。"""


class _HmacPayloadVerifier(Protocol):
    """内部 HMAC 身份校验端口，仅由固定实现承接密钥 provider。"""

    def verify(self, payload: ProtectedPayloadRef, content: bytes) -> None:
        """验证 HMAC 及其保护上下文，不得泄露密钥或正文。"""


class HmacKeyProvider(Protocol):
    """按冻结的保护上下文临时取得 HMAC 密钥。"""

    def load_hmac_key(self, protection_context_sha256: str) -> bytes:
        """返回调用期密钥；实现不得把密钥写入 bundle 或日志。"""


class CiphertextPayloadContextVerifier(Protocol):
    """由可信凭据适配器验证密文载荷的保护上下文。"""

    def verify(
        self,
        payload: ProtectedPayloadRef,
        content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        """认证密文与完整引用 AAD 的保护上下文仍有效且精确匹配。"""


class HmacPayloadIdentityVerifier:
    """以注入式密钥 provider 验证绑定正文与元数据的 HMAC 身份。"""

    def __init__(self, key_provider: HmacKeyProvider) -> None:
        if not callable(getattr(key_provider, "load_hmac_key", None)):
            raise TypeError("key_provider 必须实现 HmacKeyProvider")
        self._key_provider = key_provider

    def verify(self, payload: ProtectedPayloadRef, content: bytes) -> None:
        """只在调用期取得密钥，常量时间比对 HMAC 身份。"""
        if type(payload) is not ProtectedPayloadRef:
            raise ProtectedPayloadVerificationError("HMAC 载荷引用类型无效")
        if payload.identity_kind is not PayloadIdentityKind.HMAC_SHA256:
            raise ProtectedPayloadVerificationError("HMAC verifier 收到非 HMAC 载荷")
        if type(content) is not bytes:
            raise ProtectedPayloadVerificationError("HMAC 载荷正文类型无效")
        context_sha256 = payload.protection_context_sha256
        if type(context_sha256) is not str:
            raise ProtectedPayloadVerificationError("HMAC 载荷保护上下文无效")
        try:
            key = self._key_provider.load_hmac_key(context_sha256)
        except Exception:
            raise ProtectedPayloadVerificationError("HMAC 保护上下文不可用") from None
        if type(key) is not bytes or not key:
            raise ProtectedPayloadVerificationError("HMAC 密钥材料无效")
        actual = hmac.new(
            key,
            protected_payload_identity_material(payload, content),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(actual, payload.identity_sha256):
            raise ProtectedPayloadVerificationError("受保护载荷 HMAC 不一致")


class RuntimeProtectedPayloadVerifier:
    """在同一根租约内读取受保护叶子并执行身份策略分流。"""

    def __init__(
        self,
        *,
        hmac_key_provider: HmacKeyProvider,
        ciphertext_context_verifier: CiphertextPayloadContextVerifier,
    ) -> None:
        if not callable(getattr(ciphertext_context_verifier, "verify", None)):
            raise TypeError("ciphertext_context_verifier 必须实现 CiphertextPayloadContextVerifier")
        self._hmac_verifier = HmacPayloadIdentityVerifier(hmac_key_provider)
        self._ciphertext_context_verifier = ciphertext_context_verifier

    def verify(self, payload: ProtectedPayloadRef, *, root: BoundRuntimeRoot) -> None:
        """以同一 ``BoundRuntimeRoot`` 读取并校验一个载荷引用。"""
        if type(root) is not BoundRuntimeRoot:
            raise ProtectedPayloadVerificationError("受保护载荷根租约无效")
        payload = _normalize_payload(payload)
        content = self._read_payload(payload, root)
        if payload.identity_kind is PayloadIdentityKind.PUBLIC_SHA256:
            self._require_sha256_identity(payload, content)
            return
        if payload.identity_kind is PayloadIdentityKind.HMAC_SHA256:
            self._verify_hmac_identity(self._hmac_verifier, payload, content)
            return
        if payload.identity_kind is PayloadIdentityKind.CIPHERTEXT_SHA256:
            self._require_sha256_identity(payload, content)
            self._verify_ciphertext_context(
                self._ciphertext_context_verifier,
                payload,
                content,
            )
            return
        raise ProtectedPayloadVerificationError("受保护载荷身份类型尚未获可信校验")

    @staticmethod
    def _read_payload(payload: ProtectedPayloadRef, root: BoundRuntimeRoot) -> bytes:
        """仅用根 descriptor 相对读取；可见路径只用于派生受管组件。"""
        try:
            policy = ManagedFilePolicy(
                mode=payload.mode,
                require_uid=payload.uid,
                require_gid=payload.gid,
                max_bytes=_MAX_PROTECTED_PAYLOAD_BYTES,
            )
            return read_managed_bytes_at(
                root.path / payload.relative_path,
                root=root,
                policy=policy,
            )
        except (ManagedFileError, RuntimeRootBindingError, ValueError):
            raise ProtectedPayloadVerificationError("受保护载荷文件证据不安全") from None
        except Exception:
            raise ProtectedPayloadVerificationError("受保护载荷文件证据不安全") from None

    @staticmethod
    def _require_sha256_identity(payload: ProtectedPayloadRef, content: bytes) -> None:
        """用常量时间比较公开或密文的内容 SHA-256。"""
        actual = hashlib.sha256(content).hexdigest()
        if not hmac.compare_digest(actual, payload.identity_sha256):
            raise ProtectedPayloadVerificationError("受保护载荷摘要不一致")

    @staticmethod
    def _verify_hmac_identity(
        verifier: _HmacPayloadVerifier,
        payload: ProtectedPayloadRef,
        content: bytes,
    ) -> None:
        """收敛 HMAC 端口的任意普通异常，不暴露底层原因。"""
        try:
            verifier.verify(payload, content)
        except Exception:
            raise ProtectedPayloadVerificationError("受保护载荷秘密身份校验失败") from None

    @staticmethod
    def _verify_ciphertext_context(
        verifier: CiphertextPayloadContextVerifier,
        payload: ProtectedPayloadRef,
        content: bytes,
    ) -> None:
        """把完整引用 AAD 交给可信密文上下文端口认证。"""
        try:
            verifier.verify(
                payload,
                content,
                associated_data=ciphertext_payload_associated_data(payload),
            )
        except Exception:
            raise ProtectedPayloadVerificationError("受保护载荷密文上下文校验失败") from None


def protected_payload_identity_material(
    payload: ProtectedPayloadRef,
    content: bytes,
) -> bytes:
    """构造 HMAC 域分隔输入，绑定正文与不可替换的引用元数据。"""
    if type(payload) is not ProtectedPayloadRef:
        raise ProtectedPayloadVerificationError("HMAC 载荷引用类型无效")
    if payload.identity_kind is not PayloadIdentityKind.HMAC_SHA256:
        raise ProtectedPayloadVerificationError("HMAC 输入只接受 HMAC 载荷")
    if type(content) is not bytes:
        raise ProtectedPayloadVerificationError("HMAC 载荷正文类型无效")
    metadata = _payload_metadata(payload, include_identity_sha256=False)
    return _HMAC_DOMAIN + len(metadata).to_bytes(8, "big") + metadata + content


def ciphertext_payload_associated_data(payload: ProtectedPayloadRef) -> bytes:
    """构造密文 AEAD/等价认证端口必须验证的完整引用 AAD。"""
    if type(payload) is not ProtectedPayloadRef:
        raise ProtectedPayloadVerificationError("密文载荷引用类型无效")
    if payload.identity_kind is not PayloadIdentityKind.CIPHERTEXT_SHA256:
        raise ProtectedPayloadVerificationError("密文 AAD 只接受密文载荷")
    metadata = _payload_metadata(payload, include_identity_sha256=True)
    return b"codev-platform/runtime-protected-payload/ciphertext-aad/v1\x00" + (
        len(metadata).to_bytes(8, "big") + metadata
    )


def _normalize_payload(value: object) -> ProtectedPayloadRef:
    """重新执行公开引用的严格 codec 闭环，拒绝伪造冻结 DTO。"""
    try:
        encoded = encode_protected_payload_ref(value)
        payload = decode_protected_payload_ref(encoded)
        if encode_protected_payload_ref(payload) != encoded:
            raise ValueError("受保护载荷不是规范编码")
    except Exception:
        raise ProtectedPayloadVerificationError("受保护载荷引用无效") from None
    return payload


def _payload_metadata(
    payload: ProtectedPayloadRef,
    *,
    include_identity_sha256: bool,
) -> bytes:
    """编码两类秘密身份共用的完整、稳定引用元数据。"""
    metadata: dict[str, object] = {
        "category": payload.category.value,
        "gid": payload.gid,
        "identity_kind": payload.identity_kind.value,
        "logical_role": payload.logical_role.value,
        "mode": payload.mode,
        "protection_context_sha256": payload.protection_context_sha256,
        "relative_path": payload.relative_path,
        "target_key": payload.target_key,
        "uid": payload.uid,
    }
    if include_identity_sha256:
        metadata["identity_sha256"] = payload.identity_sha256
    return canonical_json_bytes(metadata)


__all__ = [
    "CiphertextPayloadContextVerifier",
    "HmacKeyProvider",
    "HmacPayloadIdentityVerifier",
    "ProtectedPayloadVerificationError",
    "RuntimeProtectedPayloadVerifier",
    "ciphertext_payload_associated_data",
    "protected_payload_identity_material",
]
