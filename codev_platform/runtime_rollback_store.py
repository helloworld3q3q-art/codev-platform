"""回滚包的受控不可变持久化入口。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from codev_platform._runtime_rollback_store_validation import (
    RollbackBundleConflictError,
    RuntimeRollbackStoreError,
    load_baseline_generation,
    load_frozen_attempt,
    load_optional_rollback_bundle,
    load_pending_rollback_bundle,
    load_rollback_bundle,
    normalize_public_bundle,
    require_bundle_persistence_binding,
    require_same_bundle_bytes,
)
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_control_scope_verifier import (
    RuntimeControlScopeVerificationError,
    RuntimeMutationScopeVerifier,
)
from codev_platform.runtime_attempt_contract import DeploymentAttempt
from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_generation_contract import RuntimeGeneration
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    create_managed_bytes_exclusive_at,
    remove_managed_bytes_exact_at,
)
from codev_platform.runtime_protected_payload_verifier import (
    CiphertextPayloadContextVerifier,
    HmacKeyProvider,
    RuntimeProtectedPayloadVerifier,
)
from codev_platform.runtime_rollback_contract import (
    ProtectedPayloadRef,
    RollbackBundle,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_storage import (
    RuntimeStorageError,
    rollback_bundle_path,
    rollback_bundle_pending_path,
)
from codev_platform.runtime_store_protocols import (
    RuntimeControlGate,
    RuntimeMutationScope,
    RuntimeStorePolicy,
    StoredSnapshot,
    private_managed_file_policy,
)


_BUNDLE_MAX_BYTES = 1_048_576
_ATTEMPT_MAX_BYTES = 16_384
_GENERATION_MAX_BYTES = 32_768
_Value = TypeVar("_Value")
_PRODUCTION_STORE_CONSTRUCTION_CAPABILITY = object()
_CONTROLLED_ERRORS = (
    ManagedFileError,
    PersistedActiveBootstrapLoaderError,
    RuntimeControlScopeVerificationError,
    RuntimeRootBindingError,
    RuntimeStorageError,
    ValueError,
)


class _ProtectedPayloadVerifier(Protocol):
    """内部编排核的载荷校验端口；不构成公开生产装配 API。"""

    def verify(
        self,
        payload: ProtectedPayloadRef,
        *,
        root: BoundRuntimeRoot,
    ) -> None:
        """验证 mode、uid/gid、摘要及保护上下文，失败必须抛出异常。"""


class _RuntimeRollbackStoreCore:
    """回滚 store 的内部编排核，隔离测试替身的窄注入需求。"""

    def __init__(
        self,
        policy: RuntimeStorePolicy,
        gate: RuntimeControlGate,
        payload_verifier: _ProtectedPayloadVerifier,
    ) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        if not callable(getattr(gate, "mutation", None)):
            raise TypeError("gate 必须实现 RuntimeControlGate")
        if not callable(getattr(payload_verifier, "verify", None)):
            raise TypeError("内部 payload_verifier 必须实现验证端口")
        self._policy = policy
        self._gate = gate
        self._payload_verifier = payload_verifier
        self._root = policy.root
        self._loader = PersistedActiveBootstrapLoader(policy)
        self._scope_verifier = RuntimeMutationScopeVerifier(policy)
        self._bundle_policy = private_managed_file_policy(
            policy.owner_uid,
            _BUNDLE_MAX_BYTES,
        )
        self._attempt_policy = private_managed_file_policy(
            policy.owner_uid,
            _ATTEMPT_MAX_BYTES,
        )
        self._generation_policy = private_managed_file_policy(
            policy.owner_uid,
            _GENERATION_MAX_BYTES,
        )

    def write_bundle_once(
        self,
        bundle: RollbackBundle,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[RollbackBundle]:
        """在当前活动 scope 内发布一次严格绑定的回滚 bundle。"""
        candidate = normalize_public_bundle(bundle)
        payload = self._bundle_bytes(candidate.value)

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[RollbackBundle]:
            attempt, baseline = self._load_mutation_persistence(scope, root)
            require_bundle_persistence_binding(
                candidate.value,
                attempt,
                baseline,
            )
            return self._stage_and_seal_bundle(
                candidate,
                payload,
                attempt=attempt,
                baseline=baseline,
                root=root,
            )

        return self._run_mutation(proof, operation)

    def recover_pending_bundle(
        self,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[RollbackBundle]:
        """仅凭当前活动 proof 恢复同 attempt 已持久的 pending bundle。"""

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[RollbackBundle]:
            attempt, baseline = self._load_mutation_persistence(scope, root)
            return self._recover_pending_bundle(
                attempt=attempt,
                baseline=baseline,
                root=root,
            )

        return self._run_mutation(proof, operation)

    def load_bundle(self, attempt_id: str) -> RollbackBundle:
        """短生命周期绑定后严格读取 bundle、持久绑定与全部受保护载荷。"""

        def operation(root: BoundRuntimeRoot) -> RollbackBundle:
            stored = load_rollback_bundle(
                self._root,
                attempt_id,
                root=root,
                policy=self._bundle_policy,
            )
            reservation = self._loader.load_reservation(
                attempt_id,
                bound_root=root,
            )
            attempt = load_frozen_attempt(
                self._root,
                attempt_id,
                reservation.value,
                root=root,
                policy=self._attempt_policy,
            )
            baseline = load_baseline_generation(
                self._root,
                attempt.value.baseline_generation_id,
                root=root,
                policy=self._generation_policy,
            )
            require_bundle_persistence_binding(
                stored.value,
                attempt.value,
                baseline.value,
            )
            self._verify_payloads(stored.value, root)
            return stored.value

        return self._with_bound_root(operation)

    @staticmethod
    def _bundle_bytes(bundle: RollbackBundle) -> bytes:
        """在已规范化 bundle 上取唯一完整重试字节。"""
        from codev_platform.runtime_rollback_contract import encode_rollback_bundle

        return encode_rollback_bundle(bundle)

    def _verify_payloads(self, bundle: RollbackBundle, root: BoundRuntimeRoot) -> None:
        """保持同一 bound root，逐项委托受信 verifier。"""
        for payload in (
            *bundle.systemd_payloads,
            *bundle.configuration_payloads,
            *bundle.receipt_payloads,
        ):
            self._payload_verifier.verify(payload, root=root)

    def _load_mutation_persistence(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
    ) -> tuple[DeploymentAttempt, RuntimeGeneration]:
        """在当前活动 scope 内读取冻结 attempt 和唯一基线 generation。"""
        context = self._loader.load_active_context(bound_root=root)
        attempt = load_frozen_attempt(
            self._root,
            scope.snapshot.record.attempt_id,
            context.reservation.value,
            root=root,
            policy=self._attempt_policy,
        )
        baseline = load_baseline_generation(
            self._root,
            attempt.value.baseline_generation_id,
            root=root,
            policy=self._generation_policy,
        )
        return attempt.value, baseline.value

    def _stage_and_seal_bundle(
        self,
        candidate: StoredSnapshot[RollbackBundle],
        payload: bytes,
        *,
        attempt: DeploymentAttempt,
        baseline: RuntimeGeneration,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RollbackBundle]:
        """以 pending 暂存和二次载荷校验后，才 O_EXCL 封口最终 bundle。"""
        attempt_id = candidate.value.attempt_id
        pending_path = rollback_bundle_pending_path(self._root, attempt_id)
        existing = load_optional_rollback_bundle(
            self._root,
            attempt_id,
            root=root,
            policy=self._bundle_policy,
            allow_missing_parent=True,
        )
        if existing is not None:
            require_same_bundle_bytes(payload, existing)
            require_bundle_persistence_binding(existing.value, attempt, baseline)
            self._verify_payloads(existing.value, root)
            return existing
        pending_created = False
        try:
            create_managed_bytes_exclusive_at(
                pending_path,
                payload,
                root=root,
                policy=self._bundle_policy,
            )
            pending_created = True
        except FileExistsError:
            pass
        existing = load_optional_rollback_bundle(
            self._root,
            attempt_id,
            root=root,
            policy=self._bundle_policy,
        )
        if existing is not None:
            self._require_same_or_discard_created_pending(
                payload,
                existing,
                pending_path=pending_path,
                pending_created=pending_created,
                root=root,
            )
            require_bundle_persistence_binding(existing.value, attempt, baseline)
            self._verify_payloads(existing.value, root)
            return existing
        staged = load_pending_rollback_bundle(
            self._root,
            attempt_id,
            root=root,
            policy=self._bundle_policy,
        )
        require_same_bundle_bytes(payload, staged)
        return self._verify_and_seal_pending_bundle(
            staged,
            payload,
            attempt=attempt,
            baseline=baseline,
            pending_path=pending_path,
            pending_created=pending_created,
            root=root,
        )

    def _recover_pending_bundle(
        self,
        *,
        attempt: DeploymentAttempt,
        baseline: RuntimeGeneration,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RollbackBundle]:
        """读取已持久 pending，自身携带的规范 bytes 是唯一恢复候选。"""
        attempt_id = attempt.attempt_id
        existing = load_optional_rollback_bundle(
            self._root,
            attempt_id,
            root=root,
            policy=self._bundle_policy,
        )
        if existing is not None:
            require_bundle_persistence_binding(existing.value, attempt, baseline)
            self._verify_payloads(existing.value, root)
            return existing
        staged = load_pending_rollback_bundle(
            self._root,
            attempt_id,
            root=root,
            policy=self._bundle_policy,
        )
        return self._verify_and_seal_pending_bundle(
            staged,
            self._bundle_bytes(staged.value),
            attempt=attempt,
            baseline=baseline,
            pending_path=rollback_bundle_pending_path(self._root, attempt_id),
            pending_created=False,
            root=root,
        )

    def _verify_and_seal_pending_bundle(
        self,
        staged: StoredSnapshot[RollbackBundle],
        payload: bytes,
        *,
        attempt: DeploymentAttempt,
        baseline: RuntimeGeneration,
        pending_path: Path,
        pending_created: bool,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RollbackBundle]:
        """仅在两次真实载荷校验都通过后，发布 pending 的同字节 final。"""
        attempt_id = staged.value.attempt_id
        require_bundle_persistence_binding(staged.value, attempt, baseline)
        self._verify_payloads(staged.value, root)
        self._verify_payloads(staged.value, root)
        try:
            create_managed_bytes_exclusive_at(
                rollback_bundle_path(self._root, attempt_id),
                payload,
                root=root,
                policy=self._bundle_policy,
            )
        except FileExistsError:
            existing = load_rollback_bundle(
                self._root,
                attempt_id,
                root=root,
                policy=self._bundle_policy,
            )
            self._require_same_or_discard_created_pending(
                payload,
                existing,
                pending_path=pending_path,
                pending_created=pending_created,
                root=root,
            )
            require_bundle_persistence_binding(existing.value, attempt, baseline)
            self._verify_payloads(existing.value, root)
            return existing
        return staged

    def _require_same_or_discard_created_pending(
        self,
        payload: bytes,
        existing: StoredSnapshot[RollbackBundle],
        *,
        pending_path: Path,
        pending_created: bool,
        root: BoundRuntimeRoot,
    ) -> None:
        """异值 final 冲突时，仅清理本调用刚创建且逐字节匹配的暂存叶子。"""
        try:
            require_same_bundle_bytes(payload, existing)
        except RollbackBundleConflictError:
            if pending_created:
                remove_managed_bytes_exact_at(
                    pending_path,
                    payload,
                    root=root,
                    policy=self._bundle_policy,
                )
            raise

    def _run_mutation(
        self,
        proof: ControlLeaseProof,
        operation: Callable[[RuntimeMutationScope, BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._gate.mutation(proof) as scope:
                root = self._require_mutation_scope(scope)
                with self._scope_verifier.hold_verified_mutation(
                    scope,
                    proof,
                    root=root,
                ) as verified_scope:
                    return operation(verified_scope, root)
        except RuntimeRollbackStoreError:
            raise
        except _CONTROLLED_ERRORS as error:
            raise RuntimeRollbackStoreError("回滚包写入无法安全完成") from error
        except Exception:
            raise RuntimeRollbackStoreError("回滚包写入未通过运行期安全校验") from None

    def _with_bound_root(
        self,
        operation: Callable[[BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._policy.root_binding.bind() as root:
                self._policy.root_binding.require_bound(root)
                return operation(root)
        except RuntimeRollbackStoreError:
            raise
        except _CONTROLLED_ERRORS as error:
            raise RuntimeRollbackStoreError("回滚包读取无法安全完成") from error
        except Exception:
            raise RuntimeRollbackStoreError("回滚包读取未通过运行期安全校验") from None

    def _require_mutation_scope(self, scope: object) -> BoundRuntimeRoot:
        if type(scope) is not RuntimeMutationScope:
            raise RuntimeRollbackStoreError("gate 未授予有效活动控制 scope")
        self._policy.root_binding.require_bound(scope.bound_root)
        return scope.bound_root


class RuntimeRollbackStore(_RuntimeRollbackStoreCore):
    """仅由生产 factory 构造的回滚 store，禁止直接注入 verifier。"""

    def __init__(
        self,
        policy: RuntimeStorePolicy,
        gate: RuntimeControlGate,
        *,
        hmac_key_provider: HmacKeyProvider,
        ciphertext_context_verifier: CiphertextPayloadContextVerifier,
        _construction_capability: object,
    ) -> None:
        if _construction_capability is not _PRODUCTION_STORE_CONSTRUCTION_CAPABILITY:
            raise TypeError(
                "生产 RuntimeRollbackStore 只能由 create_runtime_rollback_store 装配",
            )
        payload_verifier = RuntimeProtectedPayloadVerifier(
            hmac_key_provider=hmac_key_provider,
            ciphertext_context_verifier=ciphertext_context_verifier,
        )
        super().__init__(policy, gate, payload_verifier)

    @classmethod
    def _create_for_production(
        cls,
        policy: RuntimeStorePolicy,
        gate: RuntimeControlGate,
        *,
        hmac_key_provider: HmacKeyProvider,
        ciphertext_context_verifier: CiphertextPayloadContextVerifier,
    ) -> RuntimeRollbackStore:
        """供相邻 production composition factory 调用的私有构造路径。"""
        return cls(
            policy,
            gate,
            hmac_key_provider=hmac_key_provider,
            ciphertext_context_verifier=ciphertext_context_verifier,
            _construction_capability=_PRODUCTION_STORE_CONSTRUCTION_CAPABILITY,
        )


class _InjectedRuntimeRollbackStore(_RuntimeRollbackStoreCore):
    """仅供同模块受信内部测试注入窄替身，禁止生产调用。"""


__all__ = [
    "RollbackBundleConflictError",
    "RuntimeRollbackStore",
    "RuntimeRollbackStoreError",
]
