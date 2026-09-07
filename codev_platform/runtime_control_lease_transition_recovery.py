"""已绑定运行时根内 control lease pending transition 的语义复验与收敛。"""

from __future__ import annotations

from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseLineageError,
    verify_active_lineage,
    verify_retirement_transition,
)
from codev_platform.runtime_control_lease_persistence import ControlLeasePersistence
from codev_platform.runtime_control_lease_transition import (
    ControlLeaseTransitionIntent,
    ControlLeaseTransitionKind,
)
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    ControlLeaseStatus,
    control_lease_record_sha256,
)
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    read_managed_bytes_at,
)
from codev_platform.runtime_root_binding import (
    BoundRuntimeRoot,
    RuntimeRootBindingError,
)
from codev_platform.runtime_storage import (
    attempt_journal_path,
    attempt_terminal_evidence_path,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeStorePolicy,
    private_managed_file_policy,
)
from codev_platform.runtime_transaction_codec import (
    decode_transaction_journal,
    encode_transaction_journal,
)
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionJournal,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_terminal import verify_transaction_completion
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    decode_transaction_terminal_evidence,
    encode_transaction_terminal_evidence,
    transaction_terminal_evidence_sha256,
)
from codev_platform.runtime_transaction_terminal_validation import (
    require_evidence_completion_anchor,
    require_timestamp_after,
)


_MAX_LINEAGE_RECORDS = 128
_TerminalInputs = tuple[TransactionJournal, TransactionTerminalEvidence]


class ControlLeaseTransitionRecoveryError(RuntimeError):
    """pending transition 的持久语义、谱系或终态证据无法安全复验。"""


class ControlLeaseTransitionRecovery:
    """仅在外层已 bind、已持 deployment lock 时复验和收敛唯一 intent。"""

    def __init__(
        self,
        policy: RuntimeStorePolicy,
        persistence: ControlLeasePersistence,
    ) -> None:
        if type(policy) is not RuntimeStorePolicy:
            raise TypeError("policy 必须是 RuntimeStorePolicy")
        if type(persistence) is not ControlLeasePersistence:
            raise TypeError("persistence 必须是 ControlLeasePersistence")
        self._root = policy.root
        self._persistence = persistence
        self._bootstrap_loader = PersistedActiveBootstrapLoader(policy)
        self._terminal_policy = private_managed_file_policy(
            policy.owner_uid,
            MAX_TRANSACTION_JOURNAL_BYTES,
        )

    def reconcile(self, root: BoundRuntimeRoot) -> ControlLeaseSnapshot | None:
        """只将 pending 的唯一候选完成到 current，绝不推导替代候选。"""
        intent = self._persistence.load_pending_transition_or_none(root)
        if intent is None:
            return None
        expected = _snapshot(intent.expected_record)
        next_snapshot = _snapshot(intent.next_record)
        actual = self._persistence.load_current_or_none(root)
        if actual == next_snapshot:
            self._validate_intent(root, intent)
            self._persistence.load_history_exact(root, intent.next_record)
            self._persistence.clear_pending_transition_exact(root, intent)
            return actual
        if actual == expected:
            self._validate_intent(root, intent)
            return self._persistence.finalize_pending_transition(root, intent, expected)
        raise ControlLeaseTransitionRecoveryError(
            "pending control lease transition 与 current 不一致，拒绝猜测收敛",
        )

    def require_no_pending_transition(self, root: BoundRuntimeRoot) -> None:
        """无锁读的前后门禁；损坏 pending 由严格持久层直接闭锁。"""
        if self._persistence.load_pending_transition_or_none(root) is not None:
            raise ControlLeaseTransitionRecoveryError(
                "control lease pending transition 尚未收敛",
            )

    def load_active_context(self, root: BoundRuntimeRoot):
        """从同一根租约加载唯一活动 bootstrap context。"""
        try:
            return self._bootstrap_loader.load_active_context(bound_root=root)
        except PersistedActiveBootstrapLoaderError as error:
            raise ControlLeaseTransitionRecoveryError(
                "活动 bootstrap context 无法安全读取",
            ) from error

    def active_lineage(
        self,
        root: BoundRuntimeRoot,
        current: ControlLeaseRecord,
    ) -> tuple[ControlLeaseRecord, ...]:
        """从精确 history 回溯并复验活动 control lease 谱系。"""
        if type(current) is not ControlLeaseRecord:
            raise ControlLeaseTransitionRecoveryError("current 必须是 ControlLeaseRecord")
        if current.status is not ControlLeaseStatus.ACTIVE:
            raise ControlLeaseTransitionRecoveryError("只能重建活动 control lease lineage")
        records: list[ControlLeaseRecord] = []
        expected = current
        seen: set[str] = set()
        while True:
            snapshot = self._persistence.load_history(
                root,
                expected.attempt_id,
                control_lease_record_sha256(expected),
            )
            if snapshot.record != expected:
                raise ControlLeaseTransitionRecoveryError(
                    "control lease history 与前驱绑定漂移",
                )
            if snapshot.sha256 in seen:
                raise ControlLeaseTransitionRecoveryError("control lease history 存在循环")
            seen.add(snapshot.sha256)
            records.append(snapshot.record)
            predecessor = snapshot.record.predecessor_sha256
            if predecessor is None:
                break
            if len(records) >= _MAX_LINEAGE_RECORDS:
                raise ControlLeaseTransitionRecoveryError(
                    "control lease lineage 超出固定上限",
                )
            expected = self._persistence.load_history(
                root,
                snapshot.record.attempt_id,
                predecessor,
            ).record
        try:
            return verify_active_lineage(tuple(reversed(records)))
        except ControlLeaseLineageError as error:
            raise ControlLeaseTransitionRecoveryError(
                "control lease history lineage 无效",
            ) from error

    def require_initial_predecessor(
        self,
        root: BoundRuntimeRoot,
        previous: ControlLeaseSnapshot | None,
        next_attempt_id: str,
    ) -> None:
        """初始签发只允许空 current 或不同 attempt 的已验证 tombstone。"""
        if previous is None:
            return
        if previous.record.status is not ControlLeaseStatus.RETIRED:
            raise ControlLeaseTransitionRecoveryError(
                "已有活动 control lease，不能签发初始租约",
            )
        self.verify_retired_tombstone(root, previous)
        if previous.record.attempt_id == next_attempt_id:
            raise ControlLeaseTransitionRecoveryError(
                "同一 attempt 退休后不能重新签发初始租约",
            )

    def require_active_bootstrap_binding(self, current, reservation) -> None:
        """活动租约必须精确绑定 active envelope 派生的 reservation。"""
        if (
            current.attempt_id != reservation.value.attempt_id
            or current.reservation_sha256 != reservation.sha256
        ):
            raise ControlLeaseTransitionRecoveryError(
                "活动 bootstrap context 与 current control lease 不一致",
            )

    def load_persisted_terminal_inputs(
        self,
        root: BoundRuntimeRoot,
        attempt_id: str,
        expected: _TerminalInputs | None,
    ) -> _TerminalInputs:
        """读取规范终态；可选地复验调用方内存输入与持久字节一致。"""
        journal_payload = self._read_terminal_payload(
            root,
            attempt_journal_path(self._root, attempt_id),
            "终态 journal",
        )
        evidence_payload = self._read_terminal_payload(
            root,
            attempt_terminal_evidence_path(self._root, attempt_id),
            "终态 evidence",
        )
        try:
            journal = decode_transaction_journal(journal_payload)
            evidence = decode_transaction_terminal_evidence(evidence_payload)
        except (TypeError, ValueError) as error:
            raise ControlLeaseTransitionRecoveryError("终态输入无法严格解码") from error
        if (
            encode_transaction_journal(journal) != journal_payload
            or encode_transaction_terminal_evidence(evidence) != evidence_payload
        ):
            raise ControlLeaseTransitionRecoveryError("持久终态不是规范序列化")
        if expected is not None:
            try:
                expected_journal = encode_transaction_journal(expected[0])
                expected_evidence = encode_transaction_terminal_evidence(expected[1])
            except (TypeError, ValueError) as error:
                raise ControlLeaseTransitionRecoveryError(
                    "终态输入无法严格编码",
                ) from error
            if journal_payload != expected_journal or evidence_payload != expected_evidence:
                raise ControlLeaseTransitionRecoveryError(
                    "持久终态与内存输入不一致",
                )
        return journal, evidence

    def require_terminal_binding(
        self,
        current: ControlLeaseRecord,
        journal: TransactionJournal,
        evidence: TransactionTerminalEvidence,
        retired_at: str | None,
        lineage: tuple[ControlLeaseRecord, ...],
    ) -> None:
        """终态证据、当前活动 record 与完整谱系必须形成唯一退休边。"""
        if retired_at is None:
            raise ControlLeaseTransitionRecoveryError("retired_at 不能为空")
        try:
            verify_transaction_completion(journal, evidence)
            require_evidence_completion_anchor(evidence, lineage, current)
            require_timestamp_after(retired_at, evidence.completed_at, field="retired_at")
        except ValueError as error:
            raise ControlLeaseTransitionRecoveryError(
                "终态 journal/evidence/current/lineage 绑定无效",
            ) from error
        if (
            journal.attempt_id != current.attempt_id
            or evidence.attempt_id != current.attempt_id
            or journal.reservation_sha256 != current.reservation_sha256
            or evidence.reservation_sha256 != current.reservation_sha256
        ):
            raise ControlLeaseTransitionRecoveryError(
                "终态 journal/evidence 与 current 身份不一致",
            )

    def verify_retired_tombstone(
        self,
        root: BoundRuntimeRoot,
        tombstone: ControlLeaseSnapshot,
    ) -> None:
        """严格验证 tombstone、自身 history、前驱活动谱系及退休边。"""
        if tombstone.record.status is not ControlLeaseStatus.RETIRED:
            raise ControlLeaseTransitionRecoveryError("当前 control lease 不是 tombstone")
        self._persistence.load_history_exact(root, tombstone.record)
        predecessor = tombstone.record.retired_from_sha256
        if predecessor is None:
            raise ControlLeaseTransitionRecoveryError("tombstone 缺少退休前驱摘要")
        active = self._persistence.load_history(
            root,
            tombstone.record.attempt_id,
            predecessor,
        )
        lineage = self.active_lineage(root, active.record)
        try:
            verify_retirement_transition(lineage[-1], tombstone.record)
        except ControlLeaseLineageError as error:
            raise ControlLeaseTransitionRecoveryError(
                "tombstone 退休转换无效",
            ) from error

    def _validate_intent(
        self,
        root: BoundRuntimeRoot,
        intent: ControlLeaseTransitionIntent,
    ) -> None:
        context = self.load_active_context(root)
        expected = intent.expected_record
        if intent.kind is ControlLeaseTransitionKind.INITIAL:
            self.require_initial_predecessor(
                root,
                _snapshot(expected),
                context.reservation.value.attempt_id,
            )
            self.require_active_bootstrap_binding(
                intent.next_record,
                context.reservation,
            )
            return
        if expected is None:
            raise ControlLeaseTransitionRecoveryError(
                "pending transition 缺少活动 expected_record",
            )
        lineage = self.active_lineage(root, expected)
        self.require_active_bootstrap_binding(expected, context.reservation)
        if intent.kind is ControlLeaseTransitionKind.TAKEOVER:
            return
        journal, evidence = self.load_persisted_terminal_inputs(
            root,
            expected.attempt_id,
            None,
        )
        self.require_terminal_binding(
            expected,
            journal,
            evidence,
            intent.next_record.retired_at,
            lineage,
        )
        if intent.next_record.terminal_journal_sha256 != transaction_journal_sha256(
            journal
        ) or intent.next_record.terminal_evidence_sha256 != transaction_terminal_evidence_sha256(
            evidence
        ):
            raise ControlLeaseTransitionRecoveryError(
                "pending tombstone 与持久终态摘要不一致",
            )

    def _read_terminal_payload(
        self,
        root: BoundRuntimeRoot,
        path,
        label: str,
    ) -> bytes:
        try:
            return read_managed_bytes_at(
                path,
                root=root,
                policy=self._terminal_policy,
            )
        except (ManagedFileError, RuntimeRootBindingError) as error:
            raise ControlLeaseTransitionRecoveryError(
                f"{label} 无法安全加载",
            ) from error


def _snapshot(record: ControlLeaseRecord | None) -> ControlLeaseSnapshot | None:
    if record is None:
        return None
    return ControlLeaseSnapshot(
        record=record,
        sha256=control_lease_record_sha256(record),
    )


__all__ = [
    "ControlLeaseTransitionRecovery",
    "ControlLeaseTransitionRecoveryError",
]
