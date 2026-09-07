"""回滚包 store 的生产 verifier 装配入口。"""

from __future__ import annotations

from codev_platform.runtime_protected_payload_verifier import (
    CiphertextPayloadContextVerifier,
    HmacKeyProvider,
)
from codev_platform.runtime_rollback_store import RuntimeRollbackStore
from codev_platform.runtime_store_protocols import RuntimeControlGate, RuntimeStorePolicy


def create_runtime_rollback_store(
    policy: RuntimeStorePolicy,
    gate: RuntimeControlGate,
    *,
    hmac_key_provider: HmacKeyProvider,
    ciphertext_context_verifier: CiphertextPayloadContextVerifier,
) -> RuntimeRollbackStore:
    """装配唯一生产入口；密文端口必须认证正文与完整 AAD。"""
    return RuntimeRollbackStore._create_for_production(
        policy,
        gate,
        hmac_key_provider=hmac_key_provider,
        ciphertext_context_verifier=ciphertext_context_verifier,
    )


__all__ = ["create_runtime_rollback_store"]
