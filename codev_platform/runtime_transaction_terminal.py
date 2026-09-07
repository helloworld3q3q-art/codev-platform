"""事务 journal 的完成与 control lease 退休聚合入口。"""

from __future__ import annotations

from dataclasses import replace

from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseLineageError,
    verify_retirement_transition,
)
from codev_platform.runtime_attempt_contract import DeploymentAttempt
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    FencingContractError,
    ServingFenceRecord,
    control_lease_record_sha256,
    verify_control_lease,
)
from codev_platform.runtime_generation_acceptance import GenerationAcceptance
from codev_platform.runtime_generation_state import GenerationState
from codev_platform.runtime_serving_permit import ServingPermitRecord
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    TransactionJournalStatus,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalError as _TransactionTerminalError,
    TransactionTerminalEvidence as _TransactionTerminalEvidence,
    TransactionTerminalOutcome as _TransactionTerminalOutcome,
    transaction_terminal_evidence_sha256 as _transaction_terminal_evidence_sha256,
)
from codev_platform.runtime_transaction_terminal_validation import (
    active_view,
    derive_terminal_context,
    require_active_journal,
    require_all_actions_committed,
    require_attempt_binding,
    require_completion_timestamps,
    require_current_active_lineage,
    require_current_lease,
    require_evidence_completion_anchor,
    require_terminal_actions,
    require_timestamp_after,
)


def complete_transaction_journal(
    current: TransactionJournal,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    attempt: DeploymentAttempt,
    outcome: _TransactionTerminalOutcome,
    final_state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
    serve_permit: ServingPermitRecord | None,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    completed_at: str,
) -> tuple[TransactionJournal, _TransactionTerminalEvidence]:
    """从 typed 终态事实派生证据，并单向完成活动 journal。"""
    require_active_journal(current)
    require_current_lease(current, attempt, lease_record, lease)
    lineage = require_current_active_lineage(lease_record, control_lease_lineage)
    context = derive_terminal_context(
        outcome,
        attempt,
        final_state,
        acceptance,
        serving_fence,
        serve_permit,
        current_lease=lease_record,
        control_lease_lineage=lineage,
    )
    require_terminal_actions(
        current,
        final_state_sha256=context.final_state_sha256,
        serve_permit_sha256=context.serve_permit_sha256,
        current_lease=lease_record,
        control_lease_lineage=lineage,
    )
    require_completion_timestamps(
        completed_at,
        journal=current,
        lease_record=lease_record,
        final_state=final_state,
        acceptance=acceptance,
        serving_fence=serving_fence,
        serve_permit=serve_permit,
    )
    evidence = _TransactionTerminalEvidence(
        schema_version=1,
        attempt_id=current.attempt_id,
        reservation_sha256=current.reservation_sha256,
        deployment_attempt_sha256=context.deployment_attempt_sha256,
        journal_genesis_sha256=current.journal_genesis_sha256,
        active_journal_sha256=transaction_journal_sha256(current),
        outcome=outcome,
        final_mode=context.final_mode,
        final_serving_generation_id=context.final_serving_generation_id,
        maintenance_active=context.maintenance_active,
        final_state_sha256=context.final_state_sha256,
        acceptance_sha256=context.acceptance_sha256,
        serving_fence_sha256=context.serving_fence_sha256,
        serve_permit_sha256=context.serve_permit_sha256,
        control_lease_epoch_audit=lease_record.epoch,
        control_token_sha256_audit=lease_record.token_sha256,
        completion_control_lease_sha256=control_lease_record_sha256(lease_record),
        completed_at=completed_at,
    )
    completed = replace(
        current,
        status=TransactionJournalStatus.COMPLETED,
        terminal_evidence_sha256=_transaction_terminal_evidence_sha256(evidence),
        updated_at=completed_at,
        completed_at=completed_at,
    )
    verify_transaction_terminal_context(
        completed,
        evidence,
        attempt=attempt,
        final_state=final_state,
        acceptance=acceptance,
        serving_fence=serving_fence,
        serve_permit=serve_permit,
        current_lease=lease_record,
        control_lease_lineage=lineage,
    )
    return completed, evidence


def verify_transaction_completion(
    journal: TransactionJournal,
    evidence: _TransactionTerminalEvidence,
) -> TransactionJournal:
    """证明 completed journal 与独立终态证据精确互相绑定。"""
    if type(journal) is not TransactionJournal:
        raise _TransactionTerminalError("只接受 TransactionJournal")
    if type(evidence) is not _TransactionTerminalEvidence:
        raise _TransactionTerminalError("只接受 TransactionTerminalEvidence")
    if journal.status is not TransactionJournalStatus.COMPLETED:
        raise _TransactionTerminalError("journal 尚未完成")
    scalars_match = (
        journal.attempt_id == evidence.attempt_id
        and journal.reservation_sha256 == evidence.reservation_sha256
        and journal.journal_genesis_sha256 == evidence.journal_genesis_sha256
        and journal.completed_at == evidence.completed_at
    )
    if not scalars_match:
        raise _TransactionTerminalError("journal 与终态证据身份不一致")
    if journal.terminal_evidence_sha256 != _transaction_terminal_evidence_sha256(evidence):
        raise _TransactionTerminalError("journal 与终态证据摘要不一致")
    active = active_view(journal)
    if evidence.active_journal_sha256 != transaction_journal_sha256(active):
        raise _TransactionTerminalError("终态证据未绑定完成前 journal head")
    require_all_actions_committed(journal)
    require_timestamp_after(evidence.completed_at, active.updated_at, field="completed_at")
    return journal


def verify_transaction_terminal_context(
    journal: TransactionJournal,
    evidence: _TransactionTerminalEvidence,
    *,
    attempt: DeploymentAttempt,
    final_state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
    serve_permit: ServingPermitRecord | None,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> TransactionJournal:
    """复算类型化终态对象，拒绝孤立摘要冒充完成事实。"""
    verify_transaction_completion(journal, evidence)
    require_attempt_binding(journal, attempt)
    lineage = require_current_active_lineage(current_lease, control_lease_lineage)
    completion_lease = require_evidence_completion_anchor(
        evidence,
        lineage,
        current_lease,
    )
    context = derive_terminal_context(
        evidence.outcome,
        attempt,
        final_state,
        acceptance,
        serving_fence,
        serve_permit,
        current_lease=current_lease,
        control_lease_lineage=lineage,
    )
    require_completion_timestamps(
        evidence.completed_at,
        journal=active_view(journal),
        lease_record=completion_lease,
        final_state=final_state,
        acceptance=acceptance,
        serving_fence=serving_fence,
        serve_permit=serve_permit,
    )
    expected = (
        context.deployment_attempt_sha256,
        context.final_mode,
        context.final_serving_generation_id,
        context.maintenance_active,
        context.final_state_sha256,
        context.acceptance_sha256,
        context.serving_fence_sha256,
        context.serve_permit_sha256,
    )
    actual = (
        evidence.deployment_attempt_sha256,
        evidence.final_mode,
        evidence.final_serving_generation_id,
        evidence.maintenance_active,
        evidence.final_state_sha256,
        evidence.acceptance_sha256,
        evidence.serving_fence_sha256,
        evidence.serve_permit_sha256,
    )
    if actual != expected:
        raise _TransactionTerminalError("终态证据与类型化事实不一致")
    require_terminal_actions(
        journal,
        final_state_sha256=context.final_state_sha256,
        serve_permit_sha256=context.serve_permit_sha256,
        current_lease=current_lease,
        control_lease_lineage=lineage,
    )
    return journal


def retire_control_lease(
    current: ControlLeaseRecord,
    proof: ControlLeaseProof,
    *,
    completed_journal: TransactionJournal,
    terminal_evidence: _TransactionTerminalEvidence,
    attempt: DeploymentAttempt,
    final_state: GenerationState,
    acceptance: GenerationAcceptance | None,
    serving_fence: ServingFenceRecord,
    serve_permit: ServingPermitRecord | None,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    retired_at: str,
) -> ControlLeaseRecord:
    """验证完整终态后，以当前 capability 退休同一 attempt 的 lease。"""
    try:
        verify_control_lease(current, proof)
    except FencingContractError as exc:
        raise _TransactionTerminalError(str(exc)) from None
    lineage = require_current_active_lineage(current, control_lease_lineage)
    verify_transaction_terminal_context(
        completed_journal,
        terminal_evidence,
        attempt=attempt,
        final_state=final_state,
        acceptance=acceptance,
        serving_fence=serving_fence,
        serve_permit=serve_permit,
        current_lease=current,
        control_lease_lineage=lineage,
    )
    if (
        current.attempt_id != terminal_evidence.attempt_id
        or current.reservation_sha256 != terminal_evidence.reservation_sha256
    ):
        raise _TransactionTerminalError("control lease 与终态证据身份不一致")
    require_timestamp_after(retired_at, terminal_evidence.completed_at, field="retired_at")
    require_timestamp_after(retired_at, current.issued_at, field="retired_at")
    retired = ControlLeaseRecord(
        schema_version=current.schema_version,
        attempt_id=current.attempt_id,
        reservation_sha256=current.reservation_sha256,
        epoch=current.epoch,
        token_sha256=current.token_sha256,
        owner=current.owner,
        status=ControlLeaseStatus.RETIRED,
        issued_at=current.issued_at,
        predecessor_sha256=current.predecessor_sha256,
        terminal_journal_sha256=transaction_journal_sha256(completed_journal),
        terminal_evidence_sha256=_transaction_terminal_evidence_sha256(terminal_evidence),
        retired_at=retired_at,
        retired_from_sha256=control_lease_record_sha256(current),
    )
    try:
        return verify_retirement_transition(current, retired)
    except ControlLeaseLineageError as exc:
        raise _TransactionTerminalError(str(exc)) from None


__all__ = [
    "complete_transaction_journal",
    "retire_control_lease",
    "verify_transaction_completion",
    "verify_transaction_terminal_context",
]
