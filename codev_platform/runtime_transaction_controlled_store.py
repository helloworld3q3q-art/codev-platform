"""受 control lease scope 保护的 post-lease transaction 记录存储。"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from codev_platform._runtime_store_public_input import StorePublicInputValidator
from codev_platform.runtime_attempt_contract import (
    DeploymentAttempt,
    encode_deployment_attempt,
)
from codev_platform._runtime_transaction_controlled_validation import (
    RuntimeTransactionControlledStoreError,
    TerminalEnvelopeCleanupError,
    TransactionRecordConflictError,
    context_from_snapshots,
    decode_terminal_evidence,
    load_attempt,
    load_terminal_evidence,
    require_appended_actions_current_lease,
    require_evidence_bindings,
    require_evidence_current_head,
    require_evidence_identity,
    require_new_evidence_current_lease,
    require_expected_journal_sha,
    require_frozen_attempt,
    require_journal_context,
    require_journal_successor,
    require_same_bytes,
    require_tombstone_bootstrap,
    require_tombstone_terminal_bindings,
    snapshot,
)
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_control_scope_verifier import (
    RuntimeControlScopeVerificationError,
    RuntimeMutationScopeVerifier,
)
from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    create_managed_bytes_exclusive_at,
    read_optional_managed_bytes_at,
    remove_managed_bytes_exact_at,
    write_managed_bytes_atomic_at,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_storage import (
    RuntimeStorageError,
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_record_path,
    attempt_terminal_evidence_path,
)
from codev_platform.runtime_store_protocols import (
    ActiveBootstrapContext,
    ControlLeaseSnapshot,
    RuntimeMutationScope,
    RuntimeStorePolicy,
    RuntimeTerminalCleanupScope,
    RuntimeTransactionControlGate,
    StoredSnapshot,
    private_managed_file_policy,
)
from codev_platform.runtime_transaction_codec import encode_transaction_journal
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionJournal,
    TransactionJournalStatus,
)
from codev_platform.runtime_transaction_terminal import verify_transaction_completion
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    encode_transaction_terminal_evidence,
)


_ATTEMPT_MAX_BYTES = 16_384
_RECORD_MAX_BYTES = 32_768
_Value = TypeVar("_Value")
_CONTROLLED_ERRORS = (
    ManagedFileError,
    PersistedActiveBootstrapLoaderError,
    RuntimeControlScopeVerificationError,
    RuntimeRootBindingError,
    RuntimeStorageError,
    ValueError,
)
_INPUT = StorePublicInputValidator(RuntimeTransactionControlledStoreError)


class RuntimeTransactionStore:
    """只在 gate 注入的短生命周期 scope 内写 post-lease transaction 记录。"""

    def __init__(
        self,
        policy: RuntimeStorePolicy,
        gate: RuntimeTransactionControlGate,
    ) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        if not callable(getattr(gate, "mutation", None)) or not callable(
            getattr(gate, "terminal_cleanup", None),
        ):
            raise TypeError("gate 必须实现 RuntimeTransactionControlGate")
        self._policy = policy
        self._gate = gate
        self._root = policy.root
        self._loader = PersistedActiveBootstrapLoader(policy)
        self._scope_verifier = RuntimeMutationScopeVerifier(policy)
        self._attempt_policy = private_managed_file_policy(
            policy.owner_uid,
            _ATTEMPT_MAX_BYTES,
        )
        self._journal_policy = private_managed_file_policy(
            policy.owner_uid,
            MAX_TRANSACTION_JOURNAL_BYTES,
        )
        self._record_policy = private_managed_file_policy(
            policy.owner_uid,
            _RECORD_MAX_BYTES,
        )

    def write_attempt_once(
        self,
        attempt: DeploymentAttempt,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[DeploymentAttempt]:
        """在当前活动 scope 内不可变发布冻结 attempt。"""
        payload = _INPUT.encode(
            attempt,
            encode_deployment_attempt,
            label="冻结 attempt",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[DeploymentAttempt]:
            context = self._load_active_context(scope, root)
            require_frozen_attempt(context, attempt)
            try:
                create_managed_bytes_exclusive_at(
                    attempt_record_path(self._root, attempt.attempt_id),
                    payload,
                    root=root,
                    policy=self._attempt_policy,
                )
            except FileExistsError:
                existing = load_attempt(
                    self._root,
                    attempt.attempt_id,
                    context.reservation.value,
                    root,
                    self._attempt_policy,
                )
                require_same_bytes("冻结 attempt", payload, existing)
                return existing
            return snapshot(attempt, payload)

        return self._run_mutation(proof, operation)

    def write_journal(
        self,
        journal: TransactionJournal,
        *,
        proof: ControlLeaseProof,
        expected_sha256: str | None,
    ) -> StoredSnapshot[TransactionJournal]:
        """以严格 head SHA 作为前置条件原子推进 journal。"""
        payload = _INPUT.encode(
            journal,
            encode_transaction_journal,
            label="transaction journal",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[TransactionJournal]:
            context = self._load_active_context(scope, root)
            require_journal_context(journal, context)
            current = self._loader.load_journal_head(
                journal.attempt_id,
                bound_root=root,
            )
            require_journal_context(current.value, context)
            require_expected_journal_sha(expected_sha256, current.sha256)
            if payload == encode_transaction_journal(current.value):
                return current
            require_journal_successor(current.value, journal)
            require_appended_actions_current_lease(
                current.value,
                journal,
                scope.snapshot,
            )
            if journal.status is TransactionJournalStatus.COMPLETED:
                evidence = load_terminal_evidence(
                    self._root,
                    journal.attempt_id,
                    root,
                    self._record_policy,
                )
                attempt = load_attempt(
                    self._root,
                    journal.attempt_id,
                    context.reservation.value,
                    root,
                    self._attempt_policy,
                )
                require_evidence_bindings(evidence.value, context, attempt.value)
                if evidence.value.active_journal_sha256 != current.sha256:
                    raise TransactionRecordConflictError(
                        "终态证据未绑定当前活动 journal head",
                    )
                verify_transaction_completion(journal, evidence.value)
            write_managed_bytes_atomic_at(
                attempt_journal_path(self._root, journal.attempt_id),
                payload,
                root=root,
                policy=self._journal_policy,
            )
            return snapshot(journal, payload)

        return self._run_mutation(proof, operation)

    def write_envelope_once(
        self,
        envelope: RecoveryEnvelope,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        """确认 bootstrap 已封存的 per-attempt envelope，绝不迟到补写。"""
        payload = _INPUT.encode(
            envelope,
            encode_recovery_envelope,
            label="recovery envelope",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[RecoveryEnvelope]:
            persisted = self._load_active_context(scope, root).active_envelope
            require_same_bytes("bootstrap 恢复 envelope", payload, persisted)
            return persisted

        return self._run_mutation(proof, operation)

    def publish_active_envelope_if_absent(
        self,
        envelope: RecoveryEnvelope,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        """保留兼容入口；有效 post-lease scope 中 active envelope 必已存在。"""
        del envelope

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[RecoveryEnvelope]:
            self._load_active_context(scope, root)
            raise TransactionRecordConflictError("活动恢复 envelope 已存在")

        return self._run_mutation(proof, operation)

    def write_terminal_evidence_once(
        self,
        evidence: TransactionTerminalEvidence,
        *,
        proof: ControlLeaseProof,
    ) -> StoredSnapshot[TransactionTerminalEvidence]:
        """先于 completed journal 不可变发布终态证据。"""
        payload = _INPUT.encode(
            evidence,
            encode_transaction_terminal_evidence,
            label="terminal evidence",
        )

        def operation(
            scope: RuntimeMutationScope,
            root: BoundRuntimeRoot,
        ) -> StoredSnapshot[TransactionTerminalEvidence]:
            context = self._load_active_context(scope, root)
            current = self._loader.load_journal_head(
                evidence.attempt_id,
                bound_root=root,
            )
            require_journal_context(current.value, context)
            attempt = load_attempt(
                self._root,
                evidence.attempt_id,
                context.reservation.value,
                root,
                self._attempt_policy,
            )
            require_evidence_bindings(evidence, context, attempt.value)
            path = attempt_terminal_evidence_path(self._root, evidence.attempt_id)
            existing = read_optional_managed_bytes_at(
                path,
                root=root,
                policy=self._record_policy,
            )
            if existing is not None:
                persisted = decode_terminal_evidence(evidence.attempt_id, existing)
                require_same_bytes("终态证据", payload, persisted)
                require_evidence_current_head(persisted.value, current)
                return persisted
            if current.value.status is not TransactionJournalStatus.ACTIVE:
                raise TransactionRecordConflictError("completed journal 缺少先行终态证据")
            require_evidence_current_head(evidence, current)
            require_new_evidence_current_lease(evidence, scope.snapshot)
            try:
                create_managed_bytes_exclusive_at(
                    path,
                    payload,
                    root=root,
                    policy=self._record_policy,
                )
            except FileExistsError:
                persisted = load_terminal_evidence(
                    self._root,
                    evidence.attempt_id,
                    root,
                    self._record_policy,
                )
                require_same_bytes("终态证据", payload, persisted)
                require_evidence_current_head(persisted.value, current)
                return persisted
            return snapshot(evidence, payload)

        return self._run_mutation(proof, operation)

    def load_journal(self, attempt_id: str) -> TransactionJournal:
        """一次独立绑定后严格读取单一 journal head。"""
        return self._with_bound_root(
            lambda root: (
                self._loader.load_journal_head(
                    attempt_id,
                    bound_root=root,
                ).value
            ),
        )

    def load_terminal_evidence(self, attempt_id: str) -> TransactionTerminalEvidence:
        """一次独立绑定后严格复验 evidence 的 immutable 链，不推断完成。"""

        def operation(root: BoundRuntimeRoot) -> TransactionTerminalEvidence:
            evidence = load_terminal_evidence(
                self._root,
                attempt_id,
                root,
                self._record_policy,
            )
            reservation = self._loader.load_reservation(attempt_id, bound_root=root)
            genesis = self._loader.load_derived_journal_genesis(
                attempt_id,
                bound_root=root,
            )
            attempt = load_attempt(
                self._root,
                attempt_id,
                reservation.value,
                root,
                self._attempt_policy,
            )
            require_evidence_identity(
                evidence.value,
                reservation,
                genesis,
                attempt.value,
            )
            return evidence.value

        return self._with_bound_root(operation)

    def clear_terminal_envelope_if_current_tombstone(
        self,
        retired: ControlLeaseSnapshot,
    ) -> None:
        """只在精确 tombstone scope 内删除对应旧 active envelope。"""
        if type(retired) is not ControlLeaseSnapshot:
            raise TypeError("retired 必须是 ControlLeaseSnapshot")

        def operation(
            scope: RuntimeTerminalCleanupScope,
            root: BoundRuntimeRoot,
        ) -> None:
            if scope.snapshot != retired:
                raise TerminalEnvelopeCleanupError("终态清理 scope tombstone 已变化")
            attempt_id = retired.record.attempt_id
            journal = self._loader.load_journal_head(attempt_id, bound_root=root)
            evidence = load_terminal_evidence(
                self._root,
                attempt_id,
                root,
                self._record_policy,
            )
            verify_transaction_completion(journal.value, evidence.value)
            require_tombstone_terminal_bindings(retired, journal, evidence)
            reservation = self._loader.load_reservation(attempt_id, bound_root=root)
            genesis = self._loader.load_derived_journal_genesis(
                attempt_id,
                bound_root=root,
            )
            envelope = self._loader.load_attempt_envelope_if_present(
                attempt_id,
                bound_root=root,
            )
            if envelope is None:
                raise TerminalEnvelopeCleanupError("退休 attempt 缺少恢复 envelope")
            require_tombstone_bootstrap(retired, reservation, genesis, envelope)
            attempt = load_attempt(
                self._root,
                attempt_id,
                reservation.value,
                root,
                self._attempt_policy,
            )
            context = context_from_snapshots(reservation, genesis, envelope)
            require_evidence_bindings(evidence.value, context, attempt.value)
            path = active_recovery_envelope_path(self._root)
            observed = read_optional_managed_bytes_at(
                path,
                root=root,
                policy=self._record_policy,
            )
            if observed is None:
                return
            active = self._loader.load_active_context(bound_root=root)
            require_tombstone_bootstrap(
                retired,
                active.reservation,
                active.journal_genesis,
                active.active_envelope,
            )
            expected = encode_recovery_envelope(active.active_envelope.value)
            if observed != expected:
                raise TerminalEnvelopeCleanupError(
                    "活动恢复 envelope 已不是退休 attempt 的精确副本"
                )
            if remove_managed_bytes_exact_at(
                path,
                expected,
                root=root,
                policy=self._record_policy,
            ):
                return
            if (
                read_optional_managed_bytes_at(
                    path,
                    root=root,
                    policy=self._record_policy,
                )
                is None
            ):
                return
            raise TerminalEnvelopeCleanupError("活动恢复 envelope 精确删除未收敛")

        self._run_cleanup(retired, operation)

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
        except RuntimeTransactionControlledStoreError:
            raise
        except _CONTROLLED_ERRORS as error:
            raise RuntimeTransactionControlledStoreError(
                "受控 transaction 写入无法安全完成",
            ) from error
        except RuntimeError as error:
            raise RuntimeTransactionControlledStoreError(
                "受控 transaction 写入未获有效控制 scope",
            ) from error

    def _run_cleanup(
        self,
        retired: ControlLeaseSnapshot,
        operation: Callable[[RuntimeTerminalCleanupScope, BoundRuntimeRoot], None],
    ) -> None:
        try:
            with self._gate.terminal_cleanup(retired) as scope:
                root = self._require_cleanup_scope(scope)
                with self._scope_verifier.hold_verified_terminal_cleanup(
                    scope,
                    retired,
                    root=root,
                ) as verified_scope:
                    operation(verified_scope, root)
        except RuntimeTransactionControlledStoreError:
            raise
        except _CONTROLLED_ERRORS as error:
            raise TerminalEnvelopeCleanupError("终态 envelope 清理无法安全完成") from error
        except RuntimeError as error:
            raise TerminalEnvelopeCleanupError(
                "终态 envelope 清理未获有效控制 scope",
            ) from error

    def _with_bound_root(
        self,
        operation: Callable[[BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._policy.root_binding.bind() as root:
                return operation(root)
        except RuntimeTransactionControlledStoreError:
            raise
        except _CONTROLLED_ERRORS as error:
            raise RuntimeTransactionControlledStoreError(
                "transaction 记录无法安全读取",
            ) from error

    def _require_mutation_scope(self, scope: object) -> BoundRuntimeRoot:
        if type(scope) is not RuntimeMutationScope:
            raise RuntimeTransactionControlledStoreError("活动 control scope 类型无效")
        self._policy.root_binding.require_bound(scope.bound_root)
        return scope.bound_root

    def _require_cleanup_scope(self, scope: object) -> BoundRuntimeRoot:
        if type(scope) is not RuntimeTerminalCleanupScope:
            raise TerminalEnvelopeCleanupError("终态清理 scope 类型无效")
        self._policy.root_binding.require_bound(scope.bound_root)
        return scope.bound_root

    def _load_active_context(
        self,
        scope: RuntimeMutationScope,
        root: BoundRuntimeRoot,
    ) -> ActiveBootstrapContext:
        context = self._loader.load_active_context(bound_root=root)
        record = scope.snapshot.record
        if (
            record.attempt_id != context.reservation.value.attempt_id
            or record.reservation_sha256 != context.reservation.sha256
        ):
            raise RuntimeTransactionControlledStoreError(
                "活动 control lease 与持久 bootstrap 身份不一致",
            )
        return context


__all__ = [
    "RuntimeTransactionControlledStoreError",
    "RuntimeTransactionStore",
    "TerminalEnvelopeCleanupError",
    "TransactionRecordConflictError",
]
