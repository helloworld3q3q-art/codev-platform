"""代际切换动作的追加式事务 journal 兼容门面与命名操作。"""

from __future__ import annotations

from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    DeploymentAttempt,
    attempt_reservation_sha256,
    deployment_attempt_reservation_sha256,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    control_lease_record_sha256,
)
from codev_platform.runtime_transaction_contract_model import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    MAX_TRANSACTION_JOURNAL_BYTES,
    SERVE_PERMIT_RESOURCE_KIND,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    TransactionJournalStatus,
    TransactionRecoveryDirective,
    transaction_action_key,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_contract_validation import (
    action_identity,
    compute_genesis,
    history_for_key,
    next_action_state,
    require_action_audit,
    require_action_lineage_event,
    require_control_successor,
    require_later_timestamp,
    require_lease,
    require_live_lease_event,
    require_new_action,
)


def create_transaction_journal(
    reservation: AttemptReservation,
    *,
    created_at: str,
) -> TransactionJournal:
    """只从已持久化的 AttemptReservation 创建空 journal genesis。"""
    if type(reservation) is not AttemptReservation:
        raise TransactionContractError("只接受 AttemptReservation")
    reservation_sha256 = attempt_reservation_sha256(reservation)
    return TransactionJournal(
        schema_version=1,
        attempt_id=reservation.attempt_id,
        reservation_sha256=reservation_sha256,
        journal_genesis_sha256=compute_genesis(
            reservation.attempt_id,
            reservation_sha256,
            created_at,
        ),
        status=TransactionJournalStatus.ACTIVE,
        actions=(),
        terminal_evidence_sha256=None,
        created_at=created_at,
        updated_at=created_at,
        completed_at=None,
    )


def prepare_transaction_action(
    attempt: DeploymentAttempt,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    step_sequence: int,
    intent: TransactionActionIntent,
    resource_kind: str,
    resource_id: str,
    operation_sha256: str,
    before_sha256: str,
    after_sha256: str,
    recorded_at: str,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None = None,
) -> TransactionAction:
    """用当前控制 capability 绑定一个尚未执行的资源动作。"""
    if type(attempt) is not DeploymentAttempt:
        raise TransactionContractError("只接受 DeploymentAttempt")
    reservation_sha256 = deployment_attempt_reservation_sha256(attempt)
    require_lease(lease_record, lease, attempt.attempt_id)
    if lease_record.reservation_sha256 != reservation_sha256:
        raise TransactionContractError("DeploymentAttempt 与 control lease reservation 不一致")
    require_live_lease_event(
        lease_record,
        recorded_at,
        control_lease_lineage=control_lease_lineage,
    )
    return TransactionAction(
        schema_version=1,
        attempt_id=attempt.attempt_id,
        reservation_sha256=reservation_sha256,
        step_sequence=step_sequence,
        resource_kind=resource_kind,
        resource_id=resource_id,
        intent=intent,
        operation_sha256=operation_sha256,
        before_sha256=before_sha256,
        after_sha256=after_sha256,
        state=TransactionActionState.PREPARED,
        control_lease_epoch_audit=lease_record.epoch,
        control_token_sha256_audit=lease_record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(lease_record),
        recorded_at=recorded_at,
    )


def advance_transaction_action(
    current: TransactionAction,
    target: TransactionActionState,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    recorded_at: str,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None = None,
) -> TransactionAction:
    """只允许 PREPARED→APPLIED→COMMITTED 的显式相邻状态边。"""
    if type(current) is not TransactionAction:
        raise TransactionContractError("只接受 TransactionAction")
    if type(target) is not TransactionActionState:
        raise TransactionContractError("target 必须是 TransactionActionState")
    if target is not next_action_state(current.state):
        raise TransactionContractError("事务动作状态边无效")
    require_lease(lease_record, lease, current.attempt_id)
    if lease_record.reservation_sha256 != current.reservation_sha256:
        raise TransactionContractError("动作与 control lease reservation 不一致")
    require_later_timestamp(recorded_at, current.recorded_at, "recorded_at")
    require_control_successor(
        current.control_lease_epoch_audit,
        current.control_token_sha256_audit,
        current.control_lease_record_sha256_audit,
        lease_record.epoch,
        lease_record.token_sha256,
        control_lease_record_sha256(lease_record),
    )
    require_action_lineage_event(
        current,
        current_lease=lease_record,
        control_lease_lineage=control_lease_lineage,
    )
    require_live_lease_event(
        lease_record,
        recorded_at,
        control_lease_lineage=control_lease_lineage,
    )
    return TransactionAction(
        schema_version=current.schema_version,
        attempt_id=current.attempt_id,
        reservation_sha256=current.reservation_sha256,
        step_sequence=current.step_sequence,
        resource_kind=current.resource_kind,
        resource_id=current.resource_id,
        intent=current.intent,
        operation_sha256=current.operation_sha256,
        before_sha256=current.before_sha256,
        after_sha256=current.after_sha256,
        state=target,
        control_lease_epoch_audit=lease_record.epoch,
        control_token_sha256_audit=lease_record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(lease_record),
        recorded_at=recorded_at,
    )


def append_transaction_action(
    current: TransactionJournal,
    action: TransactionAction,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None = None,
) -> TransactionJournal:
    """校验 capability 后幂等追加一个合法动作状态。"""
    if type(current) is not TransactionJournal:
        raise TransactionContractError("只接受 TransactionJournal")
    if type(action) is not TransactionAction:
        raise TransactionContractError("只接受 TransactionAction")
    if current.status is not TransactionJournalStatus.ACTIVE:
        raise TransactionContractError("已完成 journal 禁止追加动作")
    require_lease(lease_record, lease, current.attempt_id)
    if lease_record.reservation_sha256 != current.reservation_sha256:
        raise TransactionContractError("control lease 与 journal reservation 不一致")
    if action.attempt_id != current.attempt_id:
        raise TransactionContractError("动作与 journal attempt 身份不一致")
    if action.reservation_sha256 != current.reservation_sha256:
        raise TransactionContractError("动作与 journal reservation 不一致")
    key_history = history_for_key(current.actions, transaction_action_key(action))
    existing = key_history[-1] if key_history else None
    if existing is not None and action_identity(existing) != action_identity(action):
        raise TransactionContractError("同一动作键的资源操作身份漂移")
    if any(
        record.state is action.state and action_identity(record) == action_identity(action)
        for record in key_history
    ):
        return current
    require_action_audit(action, lease_record)
    require_live_lease_event(
        lease_record,
        action.recorded_at,
        control_lease_lineage=control_lease_lineage,
    )
    if current.actions:
        latest = current.actions[-1]
        require_control_successor(
            latest.control_lease_epoch_audit,
            latest.control_token_sha256_audit,
            latest.control_lease_record_sha256_audit,
            action.control_lease_epoch_audit,
            action.control_token_sha256_audit,
            action.control_lease_record_sha256_audit,
        )
    if existing is not None:
        if action.state is not next_action_state(existing.state):
            raise TransactionContractError("事务动作状态边无效")
    else:
        require_new_action(current, action)
    return TransactionJournal(
        schema_version=current.schema_version,
        attempt_id=current.attempt_id,
        reservation_sha256=current.reservation_sha256,
        journal_genesis_sha256=current.journal_genesis_sha256,
        status=current.status,
        actions=(*current.actions, action),
        terminal_evidence_sha256=current.terminal_evidence_sha256,
        created_at=current.created_at,
        updated_at=action.recorded_at,
        completed_at=current.completed_at,
    )


def transaction_recovery_directive(
    value: TransactionJournal,
) -> TransactionRecoveryDirective:
    """给出不会直接重放 PREPARED 非幂等副作用的恢复决策。"""
    if type(value) is not TransactionJournal:
        raise TransactionContractError("只接受 TransactionJournal")
    if value.status is TransactionJournalStatus.COMPLETED:
        return TransactionRecoveryDirective.TERMINAL
    if not value.actions:
        return TransactionRecoveryDirective.START
    state = value.actions[-1].state
    if state is TransactionActionState.PREPARED:
        return TransactionRecoveryDirective.RECONCILE
    if state is TransactionActionState.APPLIED:
        return TransactionRecoveryDirective.COMMIT
    return TransactionRecoveryDirective.ADVANCE


__all__ = [
    "CURRENT_SERVE_PERMIT_RESOURCE_ID",
    "MAX_TRANSACTION_JOURNAL_BYTES",
    "SERVE_PERMIT_RESOURCE_KIND",
    "TransactionAction",
    "TransactionActionIntent",
    "TransactionActionState",
    "TransactionContractError",
    "TransactionJournal",
    "TransactionJournalStatus",
    "TransactionRecoveryDirective",
    "advance_transaction_action",
    "append_transaction_action",
    "create_transaction_journal",
    "prepare_transaction_action",
    "transaction_action_key",
    "transaction_journal_sha256",
    "transaction_recovery_directive",
]
