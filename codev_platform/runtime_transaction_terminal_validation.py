"""事务终态聚合的 typed 事实、lineage 与时间校验。"""

from __future__ import annotations

from dataclasses import dataclass, replace

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_strictly_later,
)
from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseAnchor,
    ControlLeaseLineageError,
    verify_anchor_event_window,
    verify_current_active_lineage,
)
from codev_platform.runtime_attempt_contract import (
    DeploymentAttempt,
    deployment_attempt_reservation_sha256,
    deployment_attempt_sha256,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    FencingContractError,
    ServingFenceRecord,
    verify_control_lease,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    acceptance_record_sha256,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_acceptance_validation import (
    GenerationAcceptanceBindingError,
    verify_serving_fence_acceptance,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    generation_state_sha256,
)
from codev_platform.runtime_serving_permit import (
    ServingPermitError,
    ServingPermitRecord,
    serving_permit_sha256,
    verify_serving_permit,
)
from codev_platform.runtime_transaction_contract import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    SERVE_PERMIT_RESOURCE_KIND,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionJournal,
    TransactionJournalStatus,
    transaction_action_key,
)
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalError,
    TransactionTerminalEvidence,
    TransactionTerminalOutcome,
)


_GENERATION_STATE_RESOURCE_KIND = "generation-state"
_CURRENT_GENERATION_STATE_RESOURCE_ID = "current"


@dataclass(frozen=True, slots=True)
class TerminalContext:
    """从类型化对象派生、用于终态证据比对的内部摘要集合。"""

    deployment_attempt_sha256: str
    final_mode: GenerationMode
    final_serving_generation_id: str
    maintenance_active: bool
    final_state_sha256: str
    acceptance_sha256: str | None
    serving_fence_sha256: str
    serve_permit_sha256: str | None


def derive_terminal_context(
    outcome: TransactionTerminalOutcome,
    attempt: DeploymentAttempt,
    final_state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
    serve_permit: ServingPermitRecord | None,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> TerminalContext:
    """复验 typed 终态事实，并派生不可由调用方注入的摘要集合。"""
    if type(outcome) is not TransactionTerminalOutcome:
        raise TransactionTerminalError("outcome 必须是 TransactionTerminalOutcome")
    if type(attempt) is not DeploymentAttempt:
        raise TransactionTerminalError("只接受 DeploymentAttempt")
    if type(final_state) is not GenerationState:
        raise TransactionTerminalError("只接受 GenerationState")
    if type(serving_fence) is not ServingFenceRecord:
        raise TransactionTerminalError("只接受 ServingFenceRecord")
    if acceptance is not None and type(acceptance) is not GenerationAcceptance:
        raise TransactionTerminalError("acceptance 只接受 GenerationAcceptance 或 None")
    if serve_permit is not None and type(serve_permit) is not ServingPermitRecord:
        raise TransactionTerminalError("serve_permit 只接受 ServingPermitRecord 或 None")
    _require_state_fence_acceptance(final_state, acceptance, serving_fence)
    if serve_permit is not None:
        if acceptance is None:
            raise TransactionTerminalError("ServingPermit 必须绑定 typed acceptance")
        try:
            verify_serving_permit(serve_permit, final_state, acceptance, serving_fence)
        except ServingPermitError as exc:
            raise TransactionTerminalError(str(exc)) from None
    _require_outcome_shape(
        outcome,
        attempt,
        final_state,
        acceptance,
        serve_permit,
        current_lease=current_lease,
        control_lease_lineage=control_lease_lineage,
    )
    return TerminalContext(
        deployment_attempt_sha256=deployment_attempt_sha256(attempt),
        final_mode=final_state.mode,
        final_serving_generation_id=final_state.serving_generation_id,
        maintenance_active=final_state.maintenance_active,
        final_state_sha256=generation_state_sha256(final_state),
        acceptance_sha256=(
            acceptance_record_sha256(acceptance) if acceptance is not None else None
        ),
        serving_fence_sha256=canonical_sha256(serving_fence),
        serve_permit_sha256=(
            serving_permit_sha256(serve_permit) if serve_permit is not None else None
        ),
    )


def require_active_journal(value: TransactionJournal) -> None:
    """拒绝已完成或相似对象进入完成聚合。"""
    if type(value) is not TransactionJournal:
        raise TransactionTerminalError("只接受 TransactionJournal")
    if value.status is not TransactionJournalStatus.ACTIVE:
        raise TransactionTerminalError("只允许完成活动 journal")


def require_current_lease(
    journal: TransactionJournal,
    attempt: DeploymentAttempt,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
) -> None:
    """校验 capability 与 journal/attempt 的同一 reservation 绑定。"""
    try:
        verify_control_lease(lease_record, lease)
    except FencingContractError as exc:
        raise TransactionTerminalError(str(exc)) from None
    require_attempt_binding(journal, attempt)
    if (
        lease_record.attempt_id != journal.attempt_id
        or lease_record.reservation_sha256 != journal.reservation_sha256
    ):
        raise TransactionTerminalError("control lease 与 journal reservation 不一致")


def require_attempt_binding(
    journal: TransactionJournal,
    attempt: DeploymentAttempt,
) -> None:
    """确认 typed attempt 不可替换 journal 的 reservation 真值。"""
    if type(attempt) is not DeploymentAttempt:
        raise TransactionTerminalError("只接受 DeploymentAttempt")
    if (
        attempt.attempt_id != journal.attempt_id
        or deployment_attempt_reservation_sha256(attempt) != journal.reservation_sha256
    ):
        raise TransactionTerminalError("DeploymentAttempt 与 journal reservation 不一致")


def require_terminal_actions(
    journal: TransactionJournal,
    *,
    final_state_sha256: str,
    serve_permit_sha256: str | None,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    """确认每个动作审计锚点可追溯，且最后写入终态 state。"""
    for action in journal.actions:
        _require_action_lineage_anchor(
            action,
            current_lease=current_lease,
            control_lease_lineage=control_lease_lineage,
        )
    latest = latest_actions(journal)
    if not latest:
        raise TransactionTerminalError("空 journal 不得进入终态")
    if any(action.state is not TransactionActionState.COMMITTED for action in latest):
        raise TransactionTerminalError("journal 仍有未 COMMITTED 动作")
    final_action = journal.actions[-1]
    if (
        final_action.state is not TransactionActionState.COMMITTED
        or final_action.intent is not TransactionActionIntent.APPLY_RESOURCE
        or final_action.resource_kind != _GENERATION_STATE_RESOURCE_KIND
        or final_action.resource_id != _CURRENT_GENERATION_STATE_RESOURCE_ID
        or final_action.after_sha256 != final_state_sha256
    ):
        raise TransactionTerminalError("journal 最后动作未提交最终 generation state")
    permit_actions = [
        action
        for action in latest
        if action.resource_kind == SERVE_PERMIT_RESOURCE_KIND
        and action.resource_id == CURRENT_SERVE_PERMIT_RESOURCE_ID
    ]
    if not permit_actions:
        raise TransactionTerminalError("journal 缺少 serve permit 收口动作")
    permit_action = max(permit_actions, key=lambda action: action.step_sequence)
    if serve_permit_sha256 is None:
        if permit_action.intent is not TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT:
            raise TransactionTerminalError("无 permit 终态必须以撤销动作收口")
        return
    if (
        permit_action.intent is not TransactionActionIntent.APPLY_RESOURCE
        or permit_action.after_sha256 != serve_permit_sha256
    ):
        raise TransactionTerminalError("公开稳态未提交匹配的 ServingPermit")


def latest_actions(journal: TransactionJournal) -> tuple[TransactionAction, ...]:
    """按动作键保留 journal 中的最新状态。"""
    latest: dict[str, TransactionAction] = {}
    for action in journal.actions:
        latest[transaction_action_key(action)] = action
    return tuple(latest.values())


def active_view(value: TransactionJournal) -> TransactionJournal:
    """从 completed journal 恢复其完成前的规范 active 视图。"""
    updated_at = value.actions[-1].recorded_at if value.actions else value.created_at
    return replace(
        value,
        status=TransactionJournalStatus.ACTIVE,
        terminal_evidence_sha256=None,
        updated_at=updated_at,
        completed_at=None,
    )


def require_all_actions_committed(value: TransactionJournal) -> None:
    """确认最新动作没有停留在 PREPARED/APPLIED。"""
    latest = latest_actions(value)
    if not latest:
        raise TransactionTerminalError("空 journal 不得进入终态")
    if any(action.state is not TransactionActionState.COMMITTED for action in latest):
        raise TransactionTerminalError("journal 仍有未 COMMITTED 动作")


def require_current_active_lineage(
    current_lease: ControlLeaseRecord,
    supplied: tuple[ControlLeaseRecord, ...],
) -> tuple[ControlLeaseRecord, ...]:
    """将统一 current-lineage 规则映射为终态领域错误。"""
    if type(supplied) is not tuple:
        raise TransactionTerminalError("必须显式提供 control lease lineage tuple")
    try:
        return verify_current_active_lineage(current_lease, supplied)
    except ControlLeaseLineageError as exc:
        raise TransactionTerminalError(str(exc)) from None


def require_evidence_completion_anchor(
    evidence: TransactionTerminalEvidence,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    current_lease: ControlLeaseRecord,
) -> ControlLeaseRecord:
    """确认 completed evidence 使用了 lineage 上的完整 lease 记录。"""
    try:
        return verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=evidence.completion_control_lease_sha256,
                attempt_id=evidence.attempt_id,
                reservation_sha256=evidence.reservation_sha256,
                epoch=evidence.control_lease_epoch_audit,
                token_sha256=evidence.control_token_sha256_audit,
            ),
            evidence.completed_at,
            control_lease_lineage,
            current_lease,
            event_field="evidence completed_at",
        )
    except ControlLeaseLineageError as exc:
        raise TransactionTerminalError(str(exc)) from None


def require_completion_timestamps(
    completed_at: str,
    *,
    journal: TransactionJournal,
    lease_record: ControlLeaseRecord,
    final_state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
    serve_permit: ServingPermitRecord | None,
) -> None:
    """完成时间必须严格晚于所有被终态证据引用的 typed 事实。"""
    boundaries = [
        (journal.updated_at, "journal updated_at"),
        (lease_record.issued_at, "control lease issued_at"),
        (final_state.updated_at, "最终 state updated_at"),
        (serving_fence.issued_at, "ServingFence issued_at"),
    ]
    if acceptance is not None:
        boundaries.append((acceptance.accepted_at, "acceptance accepted_at"))
    if serve_permit is not None:
        boundaries.append((serve_permit.issued_at, "ServingPermit issued_at"))
    for boundary, boundary_field in boundaries:
        require_timestamp_after(
            completed_at,
            boundary,
            field="completed_at",
            boundary_field=boundary_field,
        )


def require_timestamp_after(
    value: str,
    boundary: str,
    *,
    field: str,
    boundary_field: str = "前序审计时间",
) -> None:
    """将公共 RFC3339 严格单调约束统一映射为终态契约异常。"""
    try:
        require_strictly_later(
            value,
            boundary,
            field=field,
            boundary_field=boundary_field,
        )
    except RuntimeContractSupportError as exc:
        raise TransactionTerminalError(str(exc)) from None


def _require_state_fence_acceptance(
    state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
) -> None:
    fence_matches = (
        state.serving_generation_id == serving_fence.generation_id
        and state.serving_fence_id == serving_fence.fence_id
        and state.serving_fence_epoch == serving_fence.epoch
        and state.serving_fence_token_sha256 == serving_fence.token_sha256
    )
    if not fence_matches:
        raise TransactionTerminalError("最终 state 与 ServingFenceRecord 不一致")
    if (state.acceptance_sha256 is None) != (acceptance is None):
        raise TransactionTerminalError("最终 state 与 typed acceptance 的存在性不一致")
    if acceptance is None:
        return
    try:
        verify_serving_fence_acceptance(serving_fence, acceptance)
    except GenerationAcceptanceBindingError as exc:
        raise TransactionTerminalError(str(exc)) from None
    if state.acceptance_sha256 != serving_binding_sha256(acceptance):
        raise TransactionTerminalError("最终 state 与 acceptance 绑定不一致")


def _require_outcome_shape(
    outcome: TransactionTerminalOutcome,
    attempt: DeploymentAttempt,
    state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serve_permit: ServingPermitRecord | None,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    if outcome is TransactionTerminalOutcome.TARGET_COMMITTED:
        if (
            state.mode is not GenerationMode.STEADY
            or state.maintenance_active
            or state.serving_generation_id != attempt.target_generation_id
            or acceptance is None
            or acceptance.attempt_id != attempt.attempt_id
            or serve_permit is None
        ):
            raise TransactionTerminalError("target_committed 终态事实不完整")
        _require_acceptance_lineage_anchor(
            acceptance,
            current_lease=current_lease,
            control_lease_lineage=control_lease_lineage,
        )
        return
    if outcome is TransactionTerminalOutcome.BASELINE_RESTORED:
        if state.serving_generation_id != attempt.baseline_generation_id:
            raise TransactionTerminalError("baseline_restored 未恢复基线 generation")
        if state.mode is GenerationMode.STEADY:
            if (
                state.maintenance_active
                or acceptance is None
                or acceptance.attempt_id != attempt.attempt_id
                or serve_permit is None
            ):
                raise TransactionTerminalError("公开 baseline 稳态缺少验收或 permit")
            _require_acceptance_lineage_anchor(
                acceptance,
                current_lease=current_lease,
                control_lease_lineage=control_lease_lineage,
            )
            return
        if state.mode is GenerationMode.RESTRICTED:
            if not state.maintenance_active or serve_permit is not None:
                raise TransactionTerminalError("restricted baseline 必须关闭服务许可")
            _require_hazard_attempt_binding(
                state,
                attempt,
                current_lease=current_lease,
                control_lease_lineage=control_lease_lineage,
            )
            return
        raise TransactionTerminalError("baseline_restored mode 无效")
    if (
        state.mode is not GenerationMode.SAFETY_UNPROVEN
        or not state.maintenance_active
        or serve_permit is not None
    ):
        raise TransactionTerminalError("safety_unproven 必须关闭服务许可并保持维护态")
    _require_hazard_attempt_binding(
        state,
        attempt,
        current_lease=current_lease,
        control_lease_lineage=control_lease_lineage,
    )


def _require_hazard_attempt_binding(
    state: GenerationState,
    attempt: DeploymentAttempt,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    if (
        state.control_attempt_id != attempt.attempt_id
        or state.control_reservation_sha256 != current_lease.reservation_sha256
        or state.control_lease_record_sha256 is None
        or state.control_lease_epoch is None
        or state.control_lease_epoch > current_lease.epoch
    ):
        raise TransactionTerminalError("危险终态未绑定当前 attempt/control lease")
    try:
        verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=state.control_lease_record_sha256,
                attempt_id=state.control_attempt_id,
                reservation_sha256=state.control_reservation_sha256,
                epoch=state.control_lease_epoch,
            ),
            state.updated_at,
            control_lease_lineage,
            current_lease,
            event_field="state updated_at",
        )
    except ControlLeaseLineageError as exc:
        raise TransactionTerminalError(str(exc)) from None
    if state.desired_generation_id is None:
        return
    if state.desired_generation_id != attempt.target_generation_id:
        raise TransactionTerminalError("危险终态 desired generation 未绑定 attempt target")
    if state.rollback_generation_id != attempt.baseline_generation_id:
        raise TransactionTerminalError("危险终态 rollback generation 未绑定 attempt baseline")


def _require_action_lineage_anchor(
    action: TransactionAction,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    try:
        verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=action.control_lease_record_sha256_audit,
                attempt_id=action.attempt_id,
                reservation_sha256=action.reservation_sha256,
                epoch=action.control_lease_epoch_audit,
                token_sha256=action.control_token_sha256_audit,
            ),
            action.recorded_at,
            control_lease_lineage,
            current_lease,
            event_field="action recorded_at",
        )
    except ControlLeaseLineageError as exc:
        raise TransactionTerminalError(str(exc)) from None


def _require_acceptance_lineage_anchor(
    acceptance: GenerationAcceptance,
    *,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    try:
        verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=acceptance.control_lease_record_sha256_audit,
                attempt_id=acceptance.attempt_id,
                reservation_sha256=current_lease.reservation_sha256,
                epoch=acceptance.control_lease_epoch_audit,
                token_sha256=acceptance.control_token_sha256_audit,
            ),
            acceptance.accepted_at,
            control_lease_lineage,
            current_lease,
            event_field="acceptance accepted_at",
        )
    except ControlLeaseLineageError as exc:
        raise TransactionTerminalError(str(exc)) from None


__all__ = [
    "TerminalContext",
    "active_view",
    "derive_terminal_context",
    "latest_actions",
    "require_active_journal",
    "require_all_actions_committed",
    "require_attempt_binding",
    "require_completion_timestamps",
    "require_current_active_lineage",
    "require_current_lease",
    "require_evidence_completion_anchor",
    "require_terminal_actions",
    "require_timestamp_after",
]
