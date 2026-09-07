"""运行代际状态测试的唯一共享构造器。"""

from __future__ import annotations

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
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
    begin_switch,
    begin_validation,
    commit_serving,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)
from codev_platform.runtime_generation_state_control import (
    verify_active_control_lease_acceptance,
)


def _steady(**changes: object) -> GenerationState:
    values: dict[str, object] = {
        "schema_version": 1,
        "state_version": 7,
        "mode": GenerationMode.STEADY,
        "serving_generation_id": "a" * 64,
        "serving_fence_id": "serving-fence-a",
        "serving_fence_epoch": 11,
        "serving_fence_token_sha256": "b" * 64,
        "desired_generation_id": None,
        "rollback_generation_id": "d" * 64,
        "control_attempt_id": None,
        "control_reservation_sha256": None,
        "control_lease_record_sha256": None,
        "control_lease_epoch": None,
        "acceptance_sha256": "c" * 64,
        "maintenance_active": False,
        "updated_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return GenerationState(**values)


TOKEN = bytes(range(32))
OTHER_TOKEN = bytes(range(32, 64))


def _attempt(**changes: object) -> DeploymentAttempt:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "d" * 32,
        "operation": AttemptOperation.DEPLOY,
        "target_generation_id": "e" * 64,
        "baseline_generation_id": "a" * 64,
        "baseline_observation_sha256": "f" * 64,
        "plan_sha256": "1" * 64,
        "controller_sha256": "2" * 64,
        "created_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return DeploymentAttempt(**values)


def _reservation(**changes: object) -> AttemptReservation:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "d" * 32,
        "operation": AttemptOperation.DEPLOY,
        "plan_sha256": "1" * 64,
        "controller_sha256": "2" * 64,
        "created_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return AttemptReservation(**values)


def _control(
    *,
    attempt_id: str = "d" * 32,
) -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    return issue_control_lease(
        _reservation(attempt_id=attempt_id),
        epoch=12,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )


def _target_fence(**changes: object) -> ServingFenceRecord:
    values: dict[str, object] = {
        "schema_version": 1,
        "fence_id": "serving-fence-b",
        "generation_id": "e" * 64,
        "accepted_attempt_id": "d" * 32,
        "epoch": 12,
        "token_sha256": "3" * 64,
        "issued_at": "2026-07-19T10:03:00Z",
    }
    values.update(changes)
    return ServingFenceRecord(**values)


def _target_acceptance(
    fence: ServingFenceRecord | None = None,
) -> GenerationAcceptance:
    record = _target_fence() if fence is None else fence
    control_record, _ = _control()
    return GenerationAcceptance(
        schema_version=1,
        attempt_id=record.accepted_attempt_id,
        generation_id=record.generation_id,
        serving_fence_id=record.fence_id,
        serving_fence_epoch=record.epoch,
        serving_fence_token_sha256=record.token_sha256,
        control_lease_epoch_audit=control_record.epoch,
        control_token_sha256_audit=control_record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(control_record),
        entrypoint_proof_sha256="4" * 64,
        database_proof_sha256="5" * 64,
        systemd_proof_sha256="6" * 64,
        index_set_proof_sha256="7" * 64,
        health_proof_sha256="8" * 64,
        accepted_at="2026-07-19T10:03:30Z",
    )


def _lifecycle() -> tuple[GenerationState, ...]:
    steady = _steady()
    control_record, control_proof = _control()
    switching = begin_switch(
        steady,
        _attempt(),
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    validating = begin_validation(
        switching,
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:02:00Z",
    )
    target_fence = _target_fence()
    acceptance = _target_acceptance(target_fence)
    committed = commit_serving(
        validating,
        acceptance,
        target_fence,
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:04:00Z",
    )
    published = prepare_serving_publication(
        committed,
        serving_binding_sha256(acceptance),
        updated_at="2026-07-19T10:05:00Z",
    )
    return steady, switching, validating, committed, published


def _takeover_commit_context() -> tuple[
    GenerationState,
    GenerationAcceptance,
    ServingFenceRecord,
    ControlLeaseRecord,
    ControlLeaseRecord,
    ControlLeaseProof,
]:
    record, proof = _control()
    switching = begin_switch(
        _steady(),
        _attempt(),
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    validating = begin_validation(
        switching,
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:02:00Z",
    )
    fence = _target_fence()
    acceptance = _target_acceptance(fence)
    assert verify_active_control_lease_acceptance(record, acceptance) is record

    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=13,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:03:45Z",
    )

    return (
        validating,
        acceptance,
        fence,
        record,
        recovered_record,
        recovered_proof,
    )


def _invoke_controlled_transition(
    entry: str,
    record: ControlLeaseRecord,
    proof: object,
) -> GenerationState:
    valid_record, valid_proof = _control()
    steady = _steady()
    if entry == "begin_switch":
        return begin_switch(
            steady,
            _attempt(),
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:01:00Z",
        )
    switching = begin_switch(
        steady,
        _attempt(),
        valid_record,
        valid_proof,
        control_lease_lineage=(valid_record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    if entry == "begin_validation":
        return begin_validation(
            switching,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:02:00Z",
        )
    validating = begin_validation(
        switching,
        valid_record,
        valid_proof,
        control_lease_lineage=(valid_record,),
        updated_at="2026-07-19T10:02:00Z",
    )
    if entry == "commit_serving":
        return commit_serving(
            validating,
            _target_acceptance(),
            _target_fence(),
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:04:00Z",
        )
    transition = mark_restricted if entry == "mark_restricted" else mark_safety_unproven
    return transition(
        validating,
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:04:00Z",
    )
