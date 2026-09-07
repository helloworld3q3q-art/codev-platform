"""运行事务终态测试的共享工厂与不可变样本。"""

from __future__ import annotations

import dataclasses

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ServingFenceRecord,
    control_lease_record_sha256,
    issue_control_lease,
    recover_control_lease,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    generation_state_sha256,
    mark_restricted,
    mark_safety_unproven,
)
from codev_platform.runtime_serving_permit import (
    ServingPermitRecord,
    create_serving_permit,
    serving_permit_sha256,
)
from codev_platform import runtime_transaction_contract as transaction


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="e" * 64,
        controller_sha256="f" * 64,
        created_at="2026-07-19T10:00:00Z",
    )


def _attempt() -> DeploymentAttempt:
    return freeze_deployment_attempt(
        _reservation(),
        target_generation_id="b" * 64,
        baseline_generation_id="c" * 64,
        baseline_observation_sha256="d" * 64,
    )


def _historical_control_record(attempt_id: str) -> ControlLeaseRecord:
    """为历史公开验收构造其自身的不可变 control 审计锚点。"""
    reservation = AttemptReservation(
        schema_version=1,
        attempt_id=attempt_id,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="1" * 64,
        controller_sha256="2" * 64,
        created_at="2026-07-18T09:00:00Z",
    )
    record, _ = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32, 64)),
        owner="historical-controller",
        issued_at="2026-07-18T09:30:00Z",
    )
    return record


def _append_committed(
    journal: transaction.TransactionJournal,
    action: transaction.TransactionAction,
    record: ControlLeaseRecord,
    proof: ControlLeaseProof,
    *,
    applied_at: str,
    committed_at: str,
    control_lease_lineage: tuple[ControlLeaseRecord, ...] | None = None,
) -> transaction.TransactionJournal:
    journal = transaction.append_transaction_action(
        journal,
        action,
        record,
        proof,
        control_lease_lineage=control_lease_lineage,
    )
    applied = transaction.advance_transaction_action(
        action,
        transaction.TransactionActionState.APPLIED,
        record,
        proof,
        recorded_at=applied_at,
        control_lease_lineage=control_lease_lineage,
    )
    journal = transaction.append_transaction_action(
        journal,
        applied,
        record,
        proof,
        control_lease_lineage=control_lease_lineage,
    )
    committed = transaction.advance_transaction_action(
        applied,
        transaction.TransactionActionState.COMMITTED,
        record,
        proof,
        recorded_at=committed_at,
        control_lease_lineage=control_lease_lineage,
    )
    return transaction.append_transaction_action(
        journal,
        committed,
        record,
        proof,
        control_lease_lineage=control_lease_lineage,
    )


def _safety_terminal_input(
    mode: GenerationMode = GenerationMode.SAFETY_UNPROVEN,
) -> tuple[
    DeploymentAttempt,
    transaction.TransactionJournal,
    ControlLeaseRecord,
    ControlLeaseProof,
    GenerationState,
    GenerationAcceptance,
    ServingFenceRecord,
]:
    reservation = _reservation()
    attempt = _attempt()
    record, proof = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:00Z",
    )
    fence = ServingFenceRecord(
        schema_version=1,
        fence_id="fence-a",
        generation_id=attempt.baseline_generation_id,
        accepted_attempt_id="1" * 32,
        epoch=1,
        token_sha256="7" * 64,
        issued_at="2026-07-18T10:00:00Z",
    )
    acceptance_control = _historical_control_record(fence.accepted_attempt_id)
    acceptance = GenerationAcceptance(
        schema_version=1,
        attempt_id=fence.accepted_attempt_id,
        generation_id=fence.generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        control_lease_epoch_audit=acceptance_control.epoch,
        control_token_sha256_audit=acceptance_control.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(acceptance_control),
        entrypoint_proof_sha256="2" * 64,
        database_proof_sha256="3" * 64,
        systemd_proof_sha256="4" * 64,
        index_set_proof_sha256="5" * 64,
        health_proof_sha256="6" * 64,
        accepted_at="2026-07-18T10:01:00Z",
    )
    public_state = GenerationState(
        schema_version=1,
        state_version=1,
        mode=GenerationMode.STEADY,
        serving_generation_id=fence.generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        desired_generation_id=None,
        rollback_generation_id="9" * 64,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256=serving_binding_sha256(acceptance),
        maintenance_active=False,
        updated_at="2026-07-19T10:00:00Z",
    )
    assert mode in (GenerationMode.RESTRICTED, GenerationMode.SAFETY_UNPROVEN)
    transition = mark_restricted if mode is GenerationMode.RESTRICTED else mark_safety_unproven
    safety_state = transition(
        public_state,
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:02:00Z",
    )
    journal = transaction.create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )
    revoke = transaction.prepare_transaction_action(
        attempt,
        record,
        proof,
        step_sequence=1,
        intent=transaction.TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT,
        resource_kind=transaction.SERVE_PERMIT_RESOURCE_KIND,
        resource_id=transaction.CURRENT_SERVE_PERMIT_RESOURCE_ID,
        operation_sha256="9" * 64,
        before_sha256="a" * 64,
        after_sha256="b" * 64,
        recorded_at="2026-07-19T10:03:00Z",
    )
    journal = _append_committed(
        journal,
        revoke,
        record,
        proof,
        applied_at="2026-07-19T10:04:00Z",
        committed_at="2026-07-19T10:05:00Z",
    )
    state_action = transaction.prepare_transaction_action(
        attempt,
        record,
        proof,
        step_sequence=2,
        intent=transaction.TransactionActionIntent.APPLY_RESOURCE,
        resource_kind="generation-state",
        resource_id="current",
        operation_sha256="c" * 64,
        before_sha256=generation_state_sha256(public_state),
        after_sha256=generation_state_sha256(safety_state),
        recorded_at="2026-07-19T10:06:00Z",
    )
    journal = _append_committed(
        journal,
        state_action,
        record,
        proof,
        applied_at="2026-07-19T10:07:00Z",
        committed_at="2026-07-19T10:08:00Z",
    )
    return attempt, journal, record, proof, safety_state, acceptance, fence


def _recovered_final_action_input() -> tuple[
    DeploymentAttempt,
    transaction.TransactionJournal,
    ControlLeaseRecord,
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseProof,
    GenerationState,
    GenerationAcceptance,
    ServingFenceRecord,
]:
    attempt, full_journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=2,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-19T10:05:30Z",
    )
    first_history = tuple(action for action in full_journal.actions if action.step_sequence == 1)
    state_history = tuple(action for action in full_journal.actions if action.step_sequence == 2)
    journal = dataclasses.replace(
        full_journal,
        actions=first_history,
        updated_at=first_history[-1].recorded_at,
    )
    template = state_history[0]
    prepared = transaction.prepare_transaction_action(
        attempt,
        recovered_record,
        recovered_proof,
        step_sequence=template.step_sequence,
        intent=template.intent,
        resource_kind=template.resource_kind,
        resource_id=template.resource_id,
        operation_sha256=template.operation_sha256,
        before_sha256=template.before_sha256,
        after_sha256=template.after_sha256,
        recorded_at=template.recorded_at,
        control_lease_lineage=(record, recovered_record),
    )
    journal = _append_committed(
        journal,
        prepared,
        recovered_record,
        recovered_proof,
        applied_at=state_history[1].recorded_at,
        committed_at=state_history[2].recorded_at,
        control_lease_lineage=(record, recovered_record),
    )
    return (
        attempt,
        journal,
        record,
        proof,
        recovered_record,
        recovered_proof,
        state,
        acceptance,
        fence,
    )


def _public_terminal_input(
    *,
    target_committed: bool,
) -> tuple[
    DeploymentAttempt,
    transaction.TransactionJournal,
    ControlLeaseRecord,
    ControlLeaseProof,
    GenerationState,
    GenerationAcceptance,
    ServingFenceRecord,
    ServingPermitRecord,
]:
    reservation = _reservation()
    attempt = _attempt()
    record, proof = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:00Z",
    )
    generation_id = (
        attempt.target_generation_id if target_committed else attempt.baseline_generation_id
    )
    fence = ServingFenceRecord(
        schema_version=1,
        fence_id="fence-target" if target_committed else "fence-baseline",
        generation_id=generation_id,
        accepted_attempt_id=attempt.attempt_id,
        epoch=2,
        token_sha256="7" * 64,
        issued_at="2026-07-19T10:02:00Z",
    )
    acceptance_control = record
    acceptance = GenerationAcceptance(
        schema_version=1,
        attempt_id=fence.accepted_attempt_id,
        generation_id=generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        control_lease_epoch_audit=acceptance_control.epoch,
        control_token_sha256_audit=acceptance_control.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(acceptance_control),
        entrypoint_proof_sha256="2" * 64,
        database_proof_sha256="3" * 64,
        systemd_proof_sha256="4" * 64,
        index_set_proof_sha256="5" * 64,
        health_proof_sha256="6" * 64,
        accepted_at="2026-07-19T10:02:30Z",
    )
    state = GenerationState(
        schema_version=1,
        state_version=4,
        mode=GenerationMode.STEADY,
        serving_generation_id=generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        desired_generation_id=None,
        rollback_generation_id="9" * 64,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256=serving_binding_sha256(acceptance),
        maintenance_active=False,
        updated_at="2026-07-19T10:08:30Z",
    )
    permit = create_serving_permit(
        state,
        acceptance,
        fence,
        issued_at="2026-07-19T10:08:45Z",
    )
    journal = transaction.create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )
    actions = (
        transaction.prepare_transaction_action(
            attempt,
            record,
            proof,
            step_sequence=1,
            intent=transaction.TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT,
            resource_kind=transaction.SERVE_PERMIT_RESOURCE_KIND,
            resource_id=transaction.CURRENT_SERVE_PERMIT_RESOURCE_ID,
            operation_sha256="8" * 64,
            before_sha256="9" * 64,
            after_sha256="a" * 64,
            recorded_at="2026-07-19T10:03:00Z",
        ),
        transaction.prepare_transaction_action(
            attempt,
            record,
            proof,
            step_sequence=2,
            intent=transaction.TransactionActionIntent.APPLY_RESOURCE,
            resource_kind=transaction.SERVE_PERMIT_RESOURCE_KIND,
            resource_id=transaction.CURRENT_SERVE_PERMIT_RESOURCE_ID,
            operation_sha256="b" * 64,
            before_sha256="c" * 64,
            after_sha256=serving_permit_sha256(permit),
            recorded_at="2026-07-19T10:06:00Z",
        ),
        transaction.prepare_transaction_action(
            attempt,
            record,
            proof,
            step_sequence=3,
            intent=transaction.TransactionActionIntent.APPLY_RESOURCE,
            resource_kind="generation-state",
            resource_id="current",
            operation_sha256="d" * 64,
            before_sha256="e" * 64,
            after_sha256=generation_state_sha256(state),
            recorded_at="2026-07-19T10:09:00Z",
        ),
    )
    for action, applied_at, committed_at in zip(
        actions,
        (
            "2026-07-19T10:04:00Z",
            "2026-07-19T10:07:00Z",
            "2026-07-19T10:10:00Z",
        ),
        (
            "2026-07-19T10:05:00Z",
            "2026-07-19T10:08:00Z",
            "2026-07-19T10:11:00Z",
        ),
        strict=True,
    ):
        journal = _append_committed(
            journal,
            action,
            record,
            proof,
            applied_at=applied_at,
            committed_at=committed_at,
        )
    return attempt, journal, record, proof, state, acceptance, fence, permit
