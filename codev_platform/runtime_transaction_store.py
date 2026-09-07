"""预租约事务 bootstrap 记录的受管持久化。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    encode_attempt_reservation,
)
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapAlreadyPublishedError,
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFilePolicy,
    create_managed_bytes_exclusive_at,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)
from codev_platform.runtime_storage import (
    RuntimeStorageError,
    RuntimeStoragePathError,
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
    deployment_lock_at,
)
from codev_platform.runtime_store_protocols import (
    ActiveBootstrapContext,
    RuntimeStorePolicy,
    StoredSnapshot,
    private_managed_file_policy,
)
from codev_platform.runtime_transaction_codec import encode_transaction_journal
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionJournal,
)


_RESERVATION_MAX_BYTES = 16_384
_ENVELOPE_MAX_BYTES = 32_768
_Value = TypeVar("_Value")


class RuntimeTransactionStoreError(RuntimeError):
    """部署事务 bootstrap 持久化失败。"""


class AttemptReservationConflictError(RuntimeTransactionStoreError):
    """attempt_id 已被任何既有目录项占用。"""


class BootstrapImmutableConflictError(RuntimeTransactionStoreError):
    """不可变 bootstrap 记录已存在但规范内容不一致。"""


class ActiveEnvelopeConflictError(RuntimeTransactionStoreError):
    """活动恢复 envelope 已存在，不能覆盖。"""


class RuntimeTransactionBootstrapStore:
    """只写入 bootstrap 链；严格读取统一委托持久 loader。"""

    def __init__(self, policy: RuntimeStorePolicy) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        self._policy = policy
        self._root = policy.root
        self._loader = PersistedActiveBootstrapLoader(policy)
        self._reservation_policy = private_managed_file_policy(
            policy.owner_uid,
            _RESERVATION_MAX_BYTES,
        )
        self._journal_policy = private_managed_file_policy(
            policy.owner_uid,
            MAX_TRANSACTION_JOURNAL_BYTES,
        )
        self._envelope_policy = private_managed_file_policy(
            policy.owner_uid,
            _ENVELOPE_MAX_BYTES,
        )

    @property
    def root(self) -> Path:
        """返回固定受管根，仅用于构造固定布局路径。"""
        return self._root

    def reserve_attempt(
        self,
        reservation: AttemptReservation,
    ) -> StoredSnapshot[AttemptReservation]:
        """以 O_EXCL 语义分配一次且不可重试的 attempt 身份。"""
        payload = encode_attempt_reservation(reservation)
        try:
            return self._with_bound_root(
                lambda root: self._reserve_attempt(reservation, payload, root),
            )
        except FileExistsError:
            raise AttemptReservationConflictError("attempt reservation 已存在") from None

    def load_reservation(self, attempt_id: str) -> StoredSnapshot[AttemptReservation]:
        """严格加载已发布 reservation，拒绝非规范载荷。"""
        try:
            return self._with_bound_root(
                lambda root: self._loader.load_reservation(
                    attempt_id,
                    bound_root=root,
                ),
            )
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def write_journal_genesis_once(
        self,
        journal: TransactionJournal,
    ) -> StoredSnapshot[TransactionJournal]:
        """首次只写空 genesis；推进后重试从严格 head 派生同一身份。"""
        payload = encode_transaction_journal(journal)
        try:
            return self._with_bound_root(
                lambda root: self._write_journal_genesis_once(
                    journal,
                    payload,
                    root,
                ),
            )
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def write_envelope_once(
        self,
        envelope: RecoveryEnvelope,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        """首次要求精确空 head；已有 immutable leaf 支持推进后的同字节重试。"""
        payload = encode_recovery_envelope(envelope)
        try:
            return self._with_bound_root(
                lambda root: self._write_envelope_once(envelope, payload, root),
            )
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def publish_active_envelope_if_absent(
        self,
        envelope: RecoveryEnvelope,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        """首次活动发布只接受精确空 head，并在部署锁内独占创建。"""
        payload = encode_recovery_envelope(envelope)
        try:
            return self._with_bound_root(
                lambda root: self._publish_active_envelope_if_absent(
                    envelope,
                    payload,
                    root,
                ),
            )
        except PersistedActiveBootstrapAlreadyPublishedError:
            raise ActiveEnvelopeConflictError("活动恢复 envelope 已存在") from None
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def load_active_envelope(self) -> StoredSnapshot[RecoveryEnvelope]:
        """严格读取活动 envelope，不取得部署锁。"""
        try:
            return self._with_bound_root(
                lambda root: self._loader.load_active_envelope(bound_root=root),
            )
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def load_active_bootstrap_context(self) -> ActiveBootstrapContext:
        """读取不重入部署锁的完整、严格活动 bootstrap 三元组。"""
        try:
            return self._with_bound_root(
                lambda root: self._loader.load_active_context(bound_root=root),
            )
        except PersistedActiveBootstrapLoaderError as error:
            raise _bootstrap_loader_failure(error) from None

    def _reserve_attempt(
        self,
        reservation: AttemptReservation,
        payload: bytes,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[AttemptReservation]:
        create_managed_bytes_exclusive_at(
            attempt_reservation_path(self._root, reservation.attempt_id),
            payload,
            root=root,
            policy=self._reservation_policy,
        )
        return _snapshot(reservation, payload)

    def _write_journal_genesis_once(
        self,
        journal: TransactionJournal,
        payload: bytes,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[TransactionJournal]:
        self._loader.validate_journal_genesis_candidate(
            journal.attempt_id,
            journal,
            bound_root=root,
        )
        path = attempt_journal_path(self._root, journal.attempt_id)
        if self._create_if_absent(path, payload, self._journal_policy, root):
            return _snapshot(journal, payload)
        existing = self._loader.load_derived_journal_genesis(
            journal.attempt_id,
            bound_root=root,
        )
        _require_same_canonical_record(
            "journal genesis",
            encode_transaction_journal(existing.value),
            payload,
        )
        return existing

    def _write_envelope_once(
        self,
        envelope: RecoveryEnvelope,
        payload: bytes,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        with deployment_lock_at(root):
            existing = self._loader.load_attempt_envelope_if_present(
                envelope.attempt_id,
                bound_root=root,
            )
            if existing is not None:
                self._loader.validate_envelope_candidate(
                    envelope.attempt_id,
                    envelope,
                    require_exact_head=False,
                    bound_root=root,
                )
                _require_same_canonical_record(
                    "恢复 envelope",
                    encode_recovery_envelope(existing.value),
                    payload,
                )
                return existing
            self._loader.validate_envelope_candidate(
                envelope.attempt_id,
                envelope,
                require_exact_head=True,
                bound_root=root,
            )
            path = attempt_recovery_envelope_path(self._root, envelope.attempt_id)
            if self._create_if_absent(path, payload, self._envelope_policy, root):
                return _snapshot(envelope, payload)
            existing = self._loader.load_attempt_envelope_if_present(
                envelope.attempt_id,
                bound_root=root,
            )
            if existing is None:
                raise RuntimeTransactionStoreError("恢复 envelope 并发创建结果缺失")
            self._loader.validate_envelope_candidate(
                envelope.attempt_id,
                envelope,
                require_exact_head=False,
                bound_root=root,
            )
            _require_same_canonical_record(
                "恢复 envelope",
                encode_recovery_envelope(existing.value),
                payload,
            )
            return existing

    def _publish_active_envelope_if_absent(
        self,
        envelope: RecoveryEnvelope,
        payload: bytes,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        with deployment_lock_at(root):
            persisted = self._loader.load_attempt_envelope_if_present(
                envelope.attempt_id,
                bound_root=root,
            )
            if persisted is None:
                raise RuntimeTransactionStoreError("活动恢复 envelope 缺少 attempt 副本")
            self._loader.require_active_envelope_absent(bound_root=root)
            self._loader.validate_envelope_candidate(
                envelope.attempt_id,
                envelope,
                require_exact_head=True,
                bound_root=root,
            )
            _require_same_canonical_record(
                "活动恢复 envelope 的 attempt 副本",
                encode_recovery_envelope(persisted.value),
                payload,
            )
            try:
                create_managed_bytes_exclusive_at(
                    active_recovery_envelope_path(self._root),
                    payload,
                    root=root,
                    policy=self._envelope_policy,
                )
            except FileExistsError:
                raise ActiveEnvelopeConflictError("活动恢复 envelope 已存在") from None
            return self._loader.load_active_envelope(bound_root=root)

    def _create_if_absent(
        self,
        path: Path,
        payload: bytes,
        policy: ManagedFilePolicy,
        root: BoundRuntimeRoot,
    ) -> bool:
        try:
            create_managed_bytes_exclusive_at(
                path,
                payload,
                root=root,
                policy=policy,
            )
        except FileExistsError:
            return False
        return True

    def _with_bound_root(
        self,
        callback: Callable[[BoundRuntimeRoot], _Value],
    ) -> _Value:
        try:
            with self._policy.root_binding.bind() as root:
                self._policy.root_binding.require_bound(root)
                value = callback(root)
                self._policy.root_binding.require_bound(root)
                return value
        except RuntimeTransactionStoreError:
            raise
        except RuntimeStoragePathError:
            raise
        except RuntimeRootBindingError as error:
            raise RuntimeTransactionStoreError("运行时根租约不可安全使用") from error
        except (ManagedFileError, RuntimeStorageError) as error:
            raise RuntimeTransactionStoreError("运行时受管存储不可安全访问") from error


def _bootstrap_loader_failure(
    error: PersistedActiveBootstrapLoaderError,
) -> RuntimeTransactionStoreError:
    """保留 loader 已脱敏的结构化失败原因。"""
    return RuntimeTransactionStoreError(str(error))


def _snapshot(value: _Value, payload: bytes) -> StoredSnapshot[_Value]:
    return StoredSnapshot(
        value=value,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _require_same_canonical_record(label: str, existing: bytes, expected: bytes) -> None:
    if existing != expected:
        raise BootstrapImmutableConflictError(f"{label} 已存在且内容漂移")


__all__ = [
    "ActiveEnvelopeConflictError",
    "AttemptReservationConflictError",
    "BootstrapImmutableConflictError",
    "RuntimeTransactionBootstrapStore",
    "RuntimeTransactionStoreError",
]
