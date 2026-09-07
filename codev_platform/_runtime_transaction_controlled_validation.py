"""受控 transaction store 的纯校验、严格解码与错误边界。"""

from __future__ import annotations

import hashlib

from codev_platform._runtime_contract_support import require_sha256
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    DeploymentAttempt,
    decode_deployment_attempt,
    deployment_attempt_sha256,
    encode_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    read_managed_bytes_at,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import (
    attempt_record_path,
    attempt_terminal_evidence_path,
)
from codev_platform.runtime_store_protocols import (
    ActiveBootstrapContext,
    ControlLeaseSnapshot,
    StoredSnapshot,
)
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    TransactionJournalStatus,
)
from codev_platform.runtime_transaction_terminal import verify_transaction_completion
from codev_platform.runtime_transaction_terminal_validation import active_view
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    decode_transaction_terminal_evidence,
    encode_transaction_terminal_evidence,
)


class RuntimeTransactionControlledStoreError(RuntimeError):
    """post-lease transaction 受控持久化或严格读取失败。"""


class TransactionRecordConflictError(RuntimeTransactionControlledStoreError):
    """不可变记录漂移或 journal CAS 前置条件不成立。"""


class TerminalEnvelopeCleanupError(RuntimeTransactionControlledStoreError):
    """退休 tombstone 未能精确证明可清理旧 active envelope。"""


def snapshot(value: object, payload: bytes) -> StoredSnapshot[object]:
    """为已严格验证的规范字节构造统一摘要快照。"""
    return StoredSnapshot(value=value, sha256=hashlib.sha256(payload).hexdigest())


def load_attempt(
    root_path: object,
    attempt_id: str,
    reservation: AttemptReservation,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[DeploymentAttempt]:
    """严格读取冻结 attempt，并复验其继承的 reservation 身份。"""
    payload = read_managed_bytes_at(
        attempt_record_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    try:
        attempt = decode_deployment_attempt(payload)
        verify_frozen_attempt(reservation, attempt)
    except ValueError as error:
        raise RuntimeTransactionControlledStoreError("冻结 attempt 无法严格绑定") from error
    if attempt.attempt_id != attempt_id:
        raise RuntimeTransactionControlledStoreError("冻结 attempt 与路径身份不一致")
    require_canonical("冻结 attempt", encode_deployment_attempt(attempt), payload)
    return _typed_snapshot(attempt, payload)


def load_terminal_evidence(
    root_path: object,
    attempt_id: str,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[TransactionTerminalEvidence]:
    """严格读取单一终态 evidence；不推断 journal 是否已经完成。"""
    payload = read_managed_bytes_at(
        attempt_terminal_evidence_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    return decode_terminal_evidence(attempt_id, payload)


def decode_terminal_evidence(
    attempt_id: str,
    payload: bytes,
) -> StoredSnapshot[TransactionTerminalEvidence]:
    """严格解码并复编码终态 evidence，拒绝路径身份漂移。"""
    try:
        evidence = decode_transaction_terminal_evidence(payload)
    except ValueError as error:
        raise RuntimeTransactionControlledStoreError("终态证据无法严格解码") from error
    if evidence.attempt_id != attempt_id:
        raise RuntimeTransactionControlledStoreError("终态证据与路径身份不一致")
    require_canonical("终态证据", encode_transaction_terminal_evidence(evidence), payload)
    return _typed_snapshot(evidence, payload)


def require_frozen_attempt(
    context: ActiveBootstrapContext,
    attempt: DeploymentAttempt,
) -> None:
    """要求待写 attempt 精确继承当前活动 reservation。"""
    try:
        verify_frozen_attempt(context.reservation.value, attempt)
    except ValueError as error:
        raise RuntimeTransactionControlledStoreError(
            "冻结 attempt 与活动 reservation 不一致"
        ) from error


def require_journal_context(
    journal: TransactionJournal,
    context: ActiveBootstrapContext,
) -> None:
    """校验 journal 的 attempt、reservation 与稳定 genesis 三元组。"""
    expected = (
        context.reservation.value.attempt_id,
        context.reservation.sha256,
        context.journal_genesis.value.journal_genesis_sha256,
    )
    actual = (
        journal.attempt_id,
        journal.reservation_sha256,
        journal.journal_genesis_sha256,
    )
    if actual != expected:
        raise RuntimeTransactionControlledStoreError("journal 与活动 bootstrap 身份不一致")


def require_evidence_bindings(
    evidence: TransactionTerminalEvidence,
    context: ActiveBootstrapContext,
    attempt: DeploymentAttempt,
) -> None:
    """校验 evidence 绑定同一 reservation、genesis 与冻结 attempt。"""
    require_evidence_identity(
        evidence,
        context.reservation,
        context.journal_genesis,
        attempt,
    )


def require_evidence_identity(
    evidence: TransactionTerminalEvidence,
    reservation: StoredSnapshot[AttemptReservation],
    genesis: StoredSnapshot[TransactionJournal],
    attempt: DeploymentAttempt,
) -> None:
    """不依赖 active envelope 地复验 evidence 的 immutable identity 绑定。"""
    expected = (
        reservation.value.attempt_id,
        reservation.sha256,
        genesis.value.journal_genesis_sha256,
        deployment_attempt_sha256(attempt),
    )
    actual = (
        evidence.attempt_id,
        evidence.reservation_sha256,
        evidence.journal_genesis_sha256,
        evidence.deployment_attempt_sha256,
    )
    if actual != expected:
        raise RuntimeTransactionControlledStoreError(
            "终态证据与活动 transaction 身份不一致",
        )


def require_evidence_current_head(
    evidence: TransactionTerminalEvidence,
    current: StoredSnapshot[TransactionJournal],
) -> None:
    """evidence 首写绑定 active head；完成后的同字节重试复验双向摘要。"""
    if current.value.status is TransactionJournalStatus.ACTIVE:
        if evidence.active_journal_sha256 != current.sha256:
            raise TransactionRecordConflictError("终态证据未绑定当前活动 journal head")
        return
    verify_transaction_completion(current.value, evidence)


def require_new_evidence_current_lease(
    evidence: TransactionTerminalEvidence,
    current_lease: ControlLeaseSnapshot,
) -> None:
    """首次 O_EXCL evidence 必须由当前 scope 的 control lease 亲自锚定。"""
    record = current_lease.record
    expected = (record.epoch, record.token_sha256, current_lease.sha256)
    actual = (
        evidence.control_lease_epoch_audit,
        evidence.control_token_sha256_audit,
        evidence.completion_control_lease_sha256,
    )
    if actual != expected:
        raise TransactionRecordConflictError("新终态证据未锚定当前 control lease")


def require_expected_journal_sha(expected: str | None, actual: str) -> None:
    """拒绝 post-lease 补写 genesis，并精确验证 journal CAS 前置摘要。"""
    if expected is None:
        raise TransactionRecordConflictError("post-lease journal 不支持 expected-absent 补写")
    try:
        require_sha256(expected, field="expected_sha256")
    except ValueError as error:
        raise TransactionRecordConflictError("journal CAS 摘要无效") from error
    if expected != actual:
        raise TransactionRecordConflictError("journal CAS 前置摘要已变化")


def require_journal_successor(
    current: TransactionJournal,
    desired: TransactionJournal,
) -> None:
    """只允许 append-only active 推进或由精确 active view 进入完成态。"""
    if current == desired:
        return
    if current.status is not TransactionJournalStatus.ACTIVE:
        raise TransactionRecordConflictError("已完成 journal 禁止再次替换")
    if desired.status is TransactionJournalStatus.COMPLETED:
        if active_view(desired) != current:
            raise TransactionRecordConflictError("completed journal 未从当前活动 head 派生")
        return
    if desired.actions[: len(current.actions)] != current.actions:
        raise TransactionRecordConflictError("journal CAS 不允许回退或改写既有动作")
    if len(desired.actions) <= len(current.actions):
        raise TransactionRecordConflictError("journal active 推进必须追加动作")


def require_appended_actions_current_lease(
    current: TransactionJournal,
    desired: TransactionJournal,
    current_lease: ControlLeaseSnapshot,
) -> None:
    """要求本次新追加的动作审计锚点精确属于持有 proof 的 current lease。"""
    additions = desired.actions[len(current.actions) :]
    if not additions:
        return
    record = current_lease.record
    expected = (record.epoch, record.token_sha256, current_lease.sha256)
    for action in additions:
        actual = (
            action.control_lease_epoch_audit,
            action.control_token_sha256_audit,
            action.control_lease_record_sha256_audit,
        )
        if actual != expected:
            raise TransactionRecordConflictError(
                "journal 新动作未绑定当前 control lease",
            )


def require_same_bytes(
    label: str,
    expected: bytes,
    existing: StoredSnapshot[object],
) -> None:
    """不可变叶子仅允许完整规范字节相同的恢复重试。"""
    if canonical_bytes(existing.value) != expected:
        raise TransactionRecordConflictError(f"{label} 已存在且内容漂移")


def require_tombstone_terminal_bindings(
    retired: ControlLeaseSnapshot,
    journal: StoredSnapshot[TransactionJournal],
    evidence: StoredSnapshot[TransactionTerminalEvidence],
) -> None:
    """比对 RETIRED tombstone 与已完成 journal/evidence 的摘要真值。"""
    record = retired.record
    expected = (
        record.attempt_id,
        record.reservation_sha256,
        record.terminal_journal_sha256,
        record.terminal_evidence_sha256,
    )
    actual = (
        journal.value.attempt_id,
        journal.value.reservation_sha256,
        journal.sha256,
        evidence.sha256,
    )
    if actual != expected:
        raise TerminalEnvelopeCleanupError("退休 tombstone 与终态记录摘要不一致")


def require_tombstone_bootstrap(
    retired: ControlLeaseSnapshot,
    reservation: StoredSnapshot[AttemptReservation],
    genesis: StoredSnapshot[TransactionJournal],
    envelope: StoredSnapshot[RecoveryEnvelope],
) -> None:
    """比对 tombstone、bootstrap reservation/genesis/envelope 的同一身份。"""
    record = retired.record
    if (
        reservation.value.attempt_id != record.attempt_id
        or reservation.sha256 != record.reservation_sha256
        or genesis.value.attempt_id != record.attempt_id
        or envelope.value.attempt_id != record.attempt_id
        or envelope.value.reservation_sha256 != record.reservation_sha256
        or envelope.value.journal_genesis_sha256 != genesis.value.journal_genesis_sha256
    ):
        raise TerminalEnvelopeCleanupError("退休 tombstone 与 bootstrap 链不一致")


def context_from_snapshots(
    reservation: StoredSnapshot[AttemptReservation],
    genesis: StoredSnapshot[TransactionJournal],
    envelope: StoredSnapshot[RecoveryEnvelope],
) -> ActiveBootstrapContext:
    """将同一严格读取链收敛为已有窄 bootstrap DTO。"""
    return ActiveBootstrapContext(
        reservation=reservation,
        journal_genesis=genesis,
        active_envelope=envelope,
    )


def require_canonical(label: str, expected: bytes, actual: bytes) -> None:
    """规范编码必须与磁盘原始字节逐字节相同。"""
    if expected != actual:
        raise RuntimeTransactionControlledStoreError(f"{label} 不是规范序列化")


def canonical_bytes(value: object) -> bytes:
    """返回本分域 immutable 记录的唯一规范字节表示。"""
    if type(value) is DeploymentAttempt:
        return encode_deployment_attempt(value)
    if type(value) is TransactionTerminalEvidence:
        return encode_transaction_terminal_evidence(value)
    if type(value) is RecoveryEnvelope:
        return encode_recovery_envelope(value)
    raise RuntimeTransactionControlledStoreError("不可比较的受控记录类型")


def _typed_snapshot(value: object, payload: bytes) -> StoredSnapshot[object]:
    return snapshot(value, payload)


__all__ = [
    "RuntimeTransactionControlledStoreError",
    "TerminalEnvelopeCleanupError",
    "TransactionRecordConflictError",
    "canonical_bytes",
    "context_from_snapshots",
    "decode_terminal_evidence",
    "load_attempt",
    "load_terminal_evidence",
    "require_evidence_bindings",
    "require_evidence_identity",
    "require_evidence_current_head",
    "require_new_evidence_current_lease",
    "require_appended_actions_current_lease",
    "require_expected_journal_sha",
    "require_frozen_attempt",
    "require_journal_context",
    "require_journal_successor",
    "require_same_bytes",
    "require_tombstone_bootstrap",
    "require_tombstone_terminal_bindings",
    "snapshot",
]
