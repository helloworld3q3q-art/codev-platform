"""回滚包生产装配与真实受保护载荷链路的集成回归。"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform.runtime_protected_payload_verifier as protected_verifier
from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    create_managed_bytes_exclusive_at,
)
from codev_platform.runtime_protected_payload_verifier import (
    RuntimeProtectedPayloadVerifier,
    ciphertext_payload_associated_data,
    protected_payload_identity_material,
)
from codev_platform.runtime_rollback_contract import (
    PayloadCategory,
    PayloadIdentityKind,
    PayloadLogicalRole,
    ProtectedPayloadRef,
    create_rollback_bundle,
)
from codev_platform.runtime_rollback_store import (
    RuntimeRollbackStore,
    RuntimeRollbackStoreError,
)
from codev_platform.runtime_rollback_store_factory import create_runtime_rollback_store
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_store_protocols import (
    RuntimeMutationScope,
    RuntimeStorePolicy,
)
from tests.runtime_rollback_store_support import build_rollback_store_context
from tests.runtime_rollback_test_support import (
    bound_facts_for_payloads,
    protected_ref,
)


_ROOT_POSIX = os.name == "posix" and os.geteuid() == 0


class _NoopGate:
    """仅用于验证生产 factory 不接受伪装的 HMAC verifier。"""

    def mutation(self, _proof: ControlLeaseProof) -> object:
        raise AssertionError("factory 构造期不应调用 gate")


class _NoopPayloadVerifier:
    """只有旧 payload verify 形状，不能充当可信密钥提供器。"""

    def verify(self, _payload: object, *, root: object) -> None:
        del root


class _NoopCiphertextVerifier:
    """仅满足密文端口形状的无操作测试替身。"""

    def verify(
        self,
        _payload: object,
        _content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        del associated_data


class _StaticHmacKeyProvider:
    """测试用调用期密钥端口，不把密钥交给 bundle 或 store。"""

    def __init__(self, key: bytes) -> None:
        self._key = key
        self.contexts: list[str] = []

    def load_hmac_key(self, protection_context_sha256: str) -> bytes:
        self.contexts.append(protection_context_sha256)
        return self._key


class _RevocableCiphertextVerifier:
    """测试可信密文适配器：认证完整 AAD，并能模拟凭据撤销。"""

    def __init__(self, payload: ProtectedPayloadRef, content: bytes) -> None:
        self._payload = payload
        self._content = content
        self.revoked = False
        self.calls = 0

    def verify(
        self,
        payload: ProtectedPayloadRef,
        content: bytes,
        *,
        associated_data: bytes,
    ) -> None:
        self.calls += 1
        if self.revoked or any(
            (
                payload != self._payload,
                content != self._content,
                associated_data != ciphertext_payload_associated_data(self._payload),
            )
        ):
            raise ValueError("密文凭据已撤销或关联数据不匹配")


class _RecordingGate:
    """转发真实 gate，同时记录写入 scope 的根能力身份。"""

    def __init__(self, delegate: ControlLeaseStore) -> None:
        self._delegate = delegate
        self.roots: list[BoundRuntimeRoot] = []

    @contextmanager
    def mutation(
        self,
        proof: ControlLeaseProof,
    ) -> Iterator[RuntimeMutationScope]:
        with self._delegate.mutation(proof) as scope:
            self.roots.append(scope.bound_root)
            yield scope


def _build_real_payloads(
    hmac_key: bytes,
) -> tuple[
    tuple[ProtectedPayloadRef, bytes],
    tuple[ProtectedPayloadRef, bytes],
    tuple[ProtectedPayloadRef, bytes],
    tuple[ProtectedPayloadRef, bytes],
]:
    """构造覆盖公开、HMAC 和密文三条生产验证分支的真实载荷。"""
    systemd_content = b"[Service]\nExecStart=/srv/codev/bin/serve\n"
    systemd = protected_ref(
        PayloadCategory.SYSTEMD,
        PayloadLogicalRole.SYSTEMD_UNIT,
        "codev-platform.service",
        identity_sha256=hashlib.sha256(systemd_content).hexdigest(),
    )
    hmac_content = b"DATABASE_URL=postgresql://runtime\n"
    unsigned_hmac = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "runtime.env.hmac",
        identity_sha256="a" * 64,
        identity_kind=PayloadIdentityKind.HMAC_SHA256,
        protection_context_sha256="c" * 64,
    )
    hmac_payload = dataclasses.replace(
        unsigned_hmac,
        identity_sha256=hmac.new(
            hmac_key,
            protected_payload_identity_material(unsigned_hmac, hmac_content),
            hashlib.sha256,
        ).hexdigest(),
    )
    ciphertext_content = b"ciphertext-v1:\x00\x91\x04\xfe"
    ciphertext_payload = protected_ref(
        PayloadCategory.CONFIGURATION,
        PayloadLogicalRole.RUNTIME_CONFIGURATION,
        "runtime.env.enc",
        identity_sha256=hashlib.sha256(ciphertext_content).hexdigest(),
        identity_kind=PayloadIdentityKind.CIPHERTEXT_SHA256,
        protection_context_sha256="d" * 64,
    )
    receipt_content = b'{"deployment":"accepted"}\n'
    receipt = protected_ref(
        PayloadCategory.RECEIPT,
        PayloadLogicalRole.DEPLOYMENT_RECEIPT,
        "deployment.json",
        identity_sha256=hashlib.sha256(receipt_content).hexdigest(),
    )
    return (
        (systemd, systemd_content),
        (hmac_payload, hmac_content),
        (ciphertext_payload, ciphertext_content),
        (receipt, receipt_content),
    )


def _write_protected_payloads(
    policy: RuntimeStorePolicy,
    payloads: tuple[tuple[ProtectedPayloadRef, bytes], ...],
) -> None:
    """在真实根租约内按引用声明写入 root:root 受管叶子。"""
    with policy.root_binding.bind() as root:
        for payload, content in payloads:
            create_managed_bytes_exclusive_at(
                root.path / payload.relative_path,
                content,
                root=root,
                policy=ManagedFilePolicy(
                    mode=payload.mode,
                    require_uid=payload.uid,
                    require_gid=payload.gid,
                    max_bytes=1_048_576,
                ),
            )


def test生产factory拒绝旧式无操作payload_verifier(tmp_path: Path) -> None:
    """生产装配必须要求密钥 provider，而不能注入只会 verify 的 fake。"""
    policy = RuntimeStorePolicy(root=tmp_path, owner_uid=0)

    with pytest.raises(TypeError, match="HmacKeyProvider"):
        create_runtime_rollback_store(
            policy,
            _NoopGate(),
            hmac_key_provider=_NoopPayloadVerifier(),
            ciphertext_context_verifier=_NoopCiphertextVerifier(),
        )

    with pytest.raises(TypeError):
        RuntimeProtectedPayloadVerifier(
            hmac_verifier=_NoopPayloadVerifier(),
            ciphertext_context_verifier=_NoopCiphertextVerifier(),
        )

    wrapped_noop_context = RuntimeProtectedPayloadVerifier(
        hmac_key_provider=_StaticHmacKeyProvider(b"test-key"),
        ciphertext_context_verifier=_NoopCiphertextVerifier(),
    )
    with pytest.raises(TypeError):
        RuntimeRollbackStore(policy, _NoopGate(), wrapped_noop_context)

    with pytest.raises(TypeError, match="只能由 create_runtime_rollback_store 装配"):
        RuntimeRollbackStore(
            policy,
            _NoopGate(),
            hmac_key_provider=_StaticHmacKeyProvider(b"test-key"),
            ciphertext_context_verifier=_NoopCiphertextVerifier(),
            _construction_capability=object(),
        )


@pytest.mark.skipif(
    not _ROOT_POSIX,
    reason="真实 root:root 生产回滚链路仅在 root WSL/Linux 运行",
)
def test生产factory以同一根能力复验真实载荷并在撤销后闭锁(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写入、读取均走 concrete verifier；密文撤销后读取不得继续放行。"""
    hmac_key = b"task6-production-hmac-key"
    systemd, hmac_payload, ciphertext_payload, receipt = _build_real_payloads(hmac_key)
    facts = bound_facts_for_payloads(
        (systemd[0],),
        (hmac_payload[0], ciphertext_payload[0]),
        (receipt[0],),
    )
    context = build_rollback_store_context(tmp_path, facts=facts, owner_uid=0)
    protected_payloads = (systemd, hmac_payload, ciphertext_payload, receipt)
    _write_protected_payloads(context.policy, protected_payloads)
    gate = _RecordingGate(context.gate)
    key_provider = _StaticHmacKeyProvider(hmac_key)
    ciphertext_verifier = _RevocableCiphertextVerifier(*ciphertext_payload)
    store = create_runtime_rollback_store(
        context.policy,
        gate,
        hmac_key_provider=key_provider,
        ciphertext_context_verifier=ciphertext_verifier,
    )
    bundle = create_rollback_bundle(
        attempt=facts.attempt,
        baseline_generation=facts.generation,
        baseline_observation=facts.observation,
        systemd_payloads=facts.systemd,
        configuration_payloads=facts.configuration,
        receipt_payloads=facts.receipts,
        created_at="2026-07-21T12:00:00Z",
    )
    original_read = protected_verifier.read_managed_bytes_at
    verifier_roots: list[BoundRuntimeRoot] = []

    def recording_read(
        path: Path,
        *,
        root: BoundRuntimeRoot,
        policy: ManagedFilePolicy,
    ) -> bytes:
        verifier_roots.append(root)
        return original_read(path, root=root, policy=policy)

    monkeypatch.setattr(protected_verifier, "read_managed_bytes_at", recording_read)

    stored = store.write_bundle_once(bundle, proof=context.proof)

    assert stored.value == bundle
    assert len(gate.roots) == 1
    assert verifier_roots
    assert all(root is gate.roots[0] for root in verifier_roots)
    assert key_provider.contexts == [hmac_payload[0].protection_context_sha256] * 2
    assert ciphertext_verifier.calls == 2

    verifier_roots.clear()
    assert store.load_bundle(bundle.attempt_id) == bundle
    assert len(verifier_roots) == len(protected_payloads)
    assert len({id(root) for root in verifier_roots}) == 1
    assert key_provider.contexts == [hmac_payload[0].protection_context_sha256] * 3
    assert ciphertext_verifier.calls == 3

    ciphertext_verifier.revoked = True
    with pytest.raises(RuntimeRollbackStoreError) as captured:
        store.load_bundle(bundle.attempt_id)

    assert captured.value.__cause__ is None
    assert "凭据已撤销" not in str(captured.value)
