"""从受管磁盘严格读取预租约 bootstrap 三元组。"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    attempt_reservation_sha256,
    decode_attempt_reservation,
    encode_attempt_reservation,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFilePolicy,
    read_managed_bytes_at,
    read_optional_managed_bytes_at,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryContractError,
    RecoveryEnvelope,
    decode_recovery_envelope,
    encode_recovery_envelope,
    verify_recovery_envelope,
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
)
from codev_platform.runtime_store_protocols import (
    ActiveBootstrapContext,
    RuntimeStorePolicy,
    StoredSnapshot,
    private_managed_file_policy,
)
from codev_platform.runtime_transaction_codec import (
    decode_transaction_journal,
    encode_transaction_journal,
)
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionJournal,
    create_transaction_journal,
)


_RESERVATION_MAX_BYTES = 16_384
_ENVELOPE_MAX_BYTES = 32_768
_Value = TypeVar("_Value")


class PersistedActiveBootstrapLoaderError(RuntimeError):
    """持久 bootstrap 真值无法被严格、安全地读取或验证。"""


class PersistedActiveBootstrapAlreadyPublishedError(
    PersistedActiveBootstrapLoaderError,
):
    """活动 envelope 已存在且其完整持久三元组已通过严格验证。"""


class PersistedActiveBootstrapLoader:
    """唯一负责严格读取 reservation、journal 与恢复 envelope 的持久真值。"""

    def __init__(self, policy: RuntimeStorePolicy) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        self._policy = policy
        self._root = policy.root
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
        """返回构造时固定的运行时根，仅用于派生受管绝对路径。"""
        return self._root

    def load_reservation(
        self,
        attempt_id: str,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[AttemptReservation]:
        """从持久磁盘严格读取指定 attempt 的 reservation。"""
        return self._with_bound_root(
            lambda root: self._load_reservation(attempt_id, root),
            bound_root=bound_root,
        )

    def load_journal_head(
        self,
        attempt_id: str,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[TransactionJournal]:
        """从持久 reservation 派生身份后严格读取当前 journal head。"""
        return self._with_bound_root(
            lambda root: self._load_journal_head_for_attempt(attempt_id, root),
            bound_root=bound_root,
        )

    def load_derived_journal_genesis(
        self,
        attempt_id: str,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[TransactionJournal]:
        """从当前持久 head 的稳定身份派生空 journal genesis。"""
        return self._with_bound_root(
            lambda root: self._derive_journal_genesis_for_attempt(attempt_id, root),
            bound_root=bound_root,
        )

    def load_exact_journal_genesis(
        self,
        attempt_id: str,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[TransactionJournal]:
        """仅在当前持久 head 就是精确空 genesis 时返回它。"""
        return self._with_bound_root(
            lambda root: self._load_exact_journal_genesis(attempt_id, root),
            bound_root=bound_root,
        )

    def load_attempt_envelope_if_present(
        self,
        attempt_id: str,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[RecoveryEnvelope] | None:
        """严格读取已有 attempt envelope；仅安全最终叶子不存在时返回 ``None``。"""
        return self._with_bound_root(
            lambda root: self._load_attempt_envelope_if_present(attempt_id, root),
            bound_root=bound_root,
        )

    def validate_journal_genesis_candidate(
        self,
        attempt_id: str,
        candidate: TransactionJournal,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[AttemptReservation]:
        """以持久 reservation 验证待发布 journal genesis，不信任候选身份。"""
        return self._with_bound_root(
            lambda root: self._validate_journal_genesis_candidate(
                attempt_id,
                candidate,
                root,
            ),
            bound_root=bound_root,
        )

    def validate_envelope_candidate(
        self,
        attempt_id: str,
        candidate: RecoveryEnvelope,
        *,
        require_exact_head: bool,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[TransactionJournal]:
        """以持久 reservation/head 验证待发布 envelope 的精确绑定。"""
        if type(require_exact_head) is not bool:
            raise TypeError("require_exact_head 必须是 bool")
        return self._with_bound_root(
            lambda root: self._validate_envelope_candidate(
                attempt_id,
                candidate,
                root,
                require_exact_head=require_exact_head,
            ),
            bound_root=bound_root,
        )

    def load_active_envelope(
        self,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        """严格读取并完整复验当前活动恢复 envelope。"""
        return self._with_bound_root(
            lambda root: self._load_active_context(root).active_envelope,
            bound_root=bound_root,
        )

    def require_active_envelope_absent(
        self,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> None:
        """仅在活动 leaf 被安全确认不存在时返回；既有异常 leaf 必须报安全错误。"""
        self._with_bound_root(
            self._require_active_envelope_absent,
            bound_root=bound_root,
        )

    def load_active_context(
        self,
        *,
        bound_root: BoundRuntimeRoot | None = None,
    ) -> ActiveBootstrapContext:
        """严格读取 active→reservation→head→attempt envelope 的完整持久链。"""
        return self._with_bound_root(
            self._load_active_context,
            bound_root=bound_root,
        )

    def _load_active_context(self, root: BoundRuntimeRoot) -> ActiveBootstrapContext:
        active_payload = self._read_payload(
            active_recovery_envelope_path(self._root),
            root,
            self._envelope_policy,
            "活动恢复 envelope",
        )
        active = self._decode_envelope(active_payload, "活动恢复 envelope")
        reservation = self._load_reservation(active.value.attempt_id, root)
        journal = self._derive_journal_genesis(
            reservation.value,
            self._load_journal_head(
                active.value.attempt_id,
                reservation.value,
                root,
            ).value,
        )
        self._verify_envelope_binding(active.value, reservation.value, journal.value)
        attempt_payload = self._read_payload(
            attempt_recovery_envelope_path(self._root, active.value.attempt_id),
            root,
            self._envelope_policy,
            "attempt 恢复 envelope",
        )
        attempt = self._decode_envelope(attempt_payload, "attempt 恢复 envelope")
        self._verify_envelope_binding(attempt.value, reservation.value, journal.value)
        if attempt_payload != active_payload:
            raise PersistedActiveBootstrapLoaderError(
                "活动恢复 envelope 与 attempt 不可变副本不一致",
            )
        return ActiveBootstrapContext(
            reservation=reservation,
            journal_genesis=journal,
            active_envelope=active,
        )

    def _require_active_envelope_absent(self, root: BoundRuntimeRoot) -> None:
        payload = self._read_optional_payload(
            active_recovery_envelope_path(self._root),
            root,
            self._envelope_policy,
            "活动恢复 envelope",
        )
        if payload is None:
            return
        self._load_active_context(root)
        raise PersistedActiveBootstrapAlreadyPublishedError(
            "活动恢复 envelope 已存在",
        )

    def _load_reservation(
        self,
        attempt_id: str,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[AttemptReservation]:
        payload = self._read_payload(
            attempt_reservation_path(self._root, attempt_id),
            root,
            self._reservation_policy,
            "attempt reservation",
        )
        try:
            reservation = decode_attempt_reservation(payload)
        except ValueError as error:
            raise PersistedActiveBootstrapLoaderError(
                "attempt reservation 无法严格解码",
            ) from error
        self._require_canonical(
            "attempt reservation",
            encode_attempt_reservation(reservation),
            payload,
        )
        if reservation.attempt_id != attempt_id:
            raise PersistedActiveBootstrapLoaderError(
                "attempt reservation 与路径身份不一致",
            )
        return StoredSnapshot(
            value=reservation,
            sha256=attempt_reservation_sha256(reservation),
        )

    def _load_journal_head_for_attempt(
        self,
        attempt_id: str,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[TransactionJournal]:
        reservation = self._load_reservation(attempt_id, root)
        return self._load_journal_head(attempt_id, reservation.value, root)

    def _load_journal_head(
        self,
        attempt_id: str,
        reservation: AttemptReservation,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[TransactionJournal]:
        payload = self._read_payload(
            attempt_journal_path(self._root, attempt_id),
            root,
            self._journal_policy,
            "journal head",
        )
        try:
            journal = decode_transaction_journal(payload)
        except ValueError as error:
            raise PersistedActiveBootstrapLoaderError(
                "journal head 无法严格解码",
            ) from error
        self._require_canonical(
            "journal head",
            encode_transaction_journal(journal),
            payload,
        )
        self._verify_journal_head(reservation, journal)
        return _snapshot(journal, payload)

    def _derive_journal_genesis_for_attempt(
        self,
        attempt_id: str,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[TransactionJournal]:
        reservation = self._load_reservation(attempt_id, root)
        head = self._load_journal_head(attempt_id, reservation.value, root)
        return self._derive_journal_genesis(reservation.value, head.value)

    def _load_exact_journal_genesis(
        self,
        attempt_id: str,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[TransactionJournal]:
        reservation = self._load_reservation(attempt_id, root)
        head = self._load_journal_head(attempt_id, reservation.value, root)
        genesis = self._derive_journal_genesis(reservation.value, head.value)
        if head.value != genesis.value:
            raise PersistedActiveBootstrapLoaderError(
                "当前 journal head 不是精确空 genesis",
            )
        return head

    def _load_attempt_envelope_if_present(
        self,
        attempt_id: str,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[RecoveryEnvelope] | None:
        reservation = self._load_reservation(attempt_id, root)
        head = self._load_journal_head(attempt_id, reservation.value, root)
        journal = self._derive_journal_genesis(reservation.value, head.value)
        payload = self._read_optional_payload(
            attempt_recovery_envelope_path(self._root, attempt_id),
            root,
            self._envelope_policy,
            "attempt 恢复 envelope",
        )
        if payload is None:
            return None
        envelope = self._decode_envelope(payload, "attempt 恢复 envelope")
        self._verify_envelope_binding(envelope.value, reservation.value, journal.value)
        return envelope

    def _validate_journal_genesis_candidate(
        self,
        attempt_id: str,
        candidate: TransactionJournal,
        root: BoundRuntimeRoot,
    ) -> StoredSnapshot[AttemptReservation]:
        if type(candidate) is not TransactionJournal:
            raise PersistedActiveBootstrapLoaderError("journal genesis 候选类型无效")
        reservation = self._load_reservation(attempt_id, root)
        expected = create_transaction_journal(
            reservation.value,
            created_at=candidate.created_at,
        )
        if candidate != expected:
            raise PersistedActiveBootstrapLoaderError(
                "journal genesis 与持久化 reservation 不精确绑定",
            )
        return reservation

    def _validate_envelope_candidate(
        self,
        attempt_id: str,
        candidate: RecoveryEnvelope,
        root: BoundRuntimeRoot,
        *,
        require_exact_head: bool,
    ) -> StoredSnapshot[TransactionJournal]:
        if type(candidate) is not RecoveryEnvelope:
            raise PersistedActiveBootstrapLoaderError("恢复 envelope 候选类型无效")
        reservation = self._load_reservation(attempt_id, root)
        journal = (
            self._load_exact_journal_genesis(attempt_id, root)
            if require_exact_head
            else self._derive_journal_genesis_for_attempt(attempt_id, root)
        )
        self._verify_envelope_binding(candidate, reservation.value, journal.value)
        return journal

    def _decode_envelope(
        self,
        payload: bytes,
        label: str,
    ) -> StoredSnapshot[RecoveryEnvelope]:
        try:
            envelope = decode_recovery_envelope(payload)
        except ValueError as error:
            raise PersistedActiveBootstrapLoaderError(
                f"{label} 无法严格解码",
            ) from error
        self._require_canonical(label, encode_recovery_envelope(envelope), payload)
        return _snapshot(envelope, payload)

    def _derive_journal_genesis(
        self,
        reservation: AttemptReservation,
        head: TransactionJournal,
    ) -> StoredSnapshot[TransactionJournal]:
        self._verify_journal_head(reservation, head)
        genesis = create_transaction_journal(reservation, created_at=head.created_at)
        if head.journal_genesis_sha256 != genesis.journal_genesis_sha256:
            raise PersistedActiveBootstrapLoaderError(
                "journal genesis 与持久化 reservation 不精确绑定",
            )
        return _snapshot(genesis, encode_transaction_journal(genesis))

    def _verify_journal_head(
        self,
        reservation: AttemptReservation,
        journal: TransactionJournal,
    ) -> None:
        if type(journal) is not TransactionJournal:
            raise PersistedActiveBootstrapLoaderError("journal head 类型无效")
        if journal.attempt_id != reservation.attempt_id:
            raise PersistedActiveBootstrapLoaderError(
                "journal head 与 reservation 身份不一致",
            )
        if journal.reservation_sha256 != attempt_reservation_sha256(reservation):
            raise PersistedActiveBootstrapLoaderError(
                "journal head 与 reservation 摘要不一致",
            )

    def _verify_envelope_binding(
        self,
        envelope: RecoveryEnvelope,
        reservation: AttemptReservation,
        journal: TransactionJournal,
    ) -> None:
        try:
            verify_recovery_envelope(envelope, reservation, journal)
        except RecoveryContractError as error:
            raise PersistedActiveBootstrapLoaderError(
                "恢复 envelope 与持久化 reservation/journal genesis 不精确绑定",
            ) from error

    def _read_payload(
        self,
        path: Path,
        root: BoundRuntimeRoot,
        policy: ManagedFilePolicy,
        label: str,
    ) -> bytes:
        try:
            return read_managed_bytes_at(path, root=root, policy=policy)
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise PersistedActiveBootstrapLoaderError(
                f"{label} 无法安全加载",
            ) from error

    def _read_optional_payload(
        self,
        path: Path,
        root: BoundRuntimeRoot,
        policy: ManagedFilePolicy,
        label: str,
    ) -> bytes | None:
        try:
            return read_optional_managed_bytes_at(path, root=root, policy=policy)
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise PersistedActiveBootstrapLoaderError(
                f"{label} 无法安全检查",
            ) from error

    def _require_canonical(self, label: str, canonical: bytes, payload: bytes) -> None:
        if canonical != payload:
            raise PersistedActiveBootstrapLoaderError(f"{label} 不是规范序列化")

    def _with_bound_root(
        self,
        callback: Callable[[BoundRuntimeRoot], _Value],
        *,
        bound_root: BoundRuntimeRoot | None,
    ) -> _Value:
        try:
            if bound_root is not None:
                return self._run_in_bound_root(callback, bound_root)
            with self._policy.root_binding.bind() as owned_root:
                return self._run_in_bound_root(callback, owned_root)
        except PersistedActiveBootstrapLoaderError:
            raise
        except RuntimeStoragePathError:
            raise
        except RuntimeRootBindingError as error:
            raise PersistedActiveBootstrapLoaderError(
                "运行时根租约不可安全使用",
            ) from error
        except (ManagedFileError, RuntimeStorageError) as error:
            raise PersistedActiveBootstrapLoaderError(
                "运行时受管存储不可安全访问",
            ) from error

    def _run_in_bound_root(
        self,
        callback: Callable[[BoundRuntimeRoot], _Value],
        root: BoundRuntimeRoot,
    ) -> _Value:
        self._policy.root_binding.require_bound(root)
        value = callback(root)
        self._policy.root_binding.require_bound(root)
        return value


def _snapshot(value: _Value, payload: bytes) -> StoredSnapshot[_Value]:
    return StoredSnapshot(
        value=value,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


__all__ = [
    "PersistedActiveBootstrapAlreadyPublishedError",
    "PersistedActiveBootstrapLoader",
    "PersistedActiveBootstrapLoaderError",
]
