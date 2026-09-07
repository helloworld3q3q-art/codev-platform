"""运行围栏领域测试共享的唯一常量与对象工厂。"""

from __future__ import annotations

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ServingFenceProof,
    ServingFenceRecord,
    control_lease_record_sha256,
    issue_control_lease,
    issue_serving_fence,
)
from codev_platform.runtime_generation_acceptance import GenerationAcceptance
from codev_platform.runtime_generation_state import GenerationMode, GenerationState


TOKEN = bytes(range(32))
OTHER_TOKEN = bytes(range(32, 64))


def _attempt(**changes: object) -> DeploymentAttempt:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "a" * 32,
        "operation": AttemptOperation.DEPLOY,
        "target_generation_id": "b" * 64,
        "baseline_generation_id": "c" * 64,
        "baseline_observation_sha256": "d" * 64,
        "plan_sha256": "e" * 64,
        "controller_sha256": "f" * 64,
        "created_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return DeploymentAttempt(**values)


def _reservation(**changes: object) -> AttemptReservation:
    values: dict[str, object] = {
        "schema_version": 1,
        "attempt_id": "a" * 32,
        "operation": AttemptOperation.DEPLOY,
        "plan_sha256": "e" * 64,
        "controller_sha256": "f" * 64,
        "created_at": "2026-07-19T10:00:00Z",
    }
    values.update(changes)
    return AttemptReservation(**values)


def _control() -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    return issue_control_lease(
        _reservation(),
        epoch=4,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )


def _serving() -> tuple[ServingFenceRecord, ServingFenceProof]:
    return issue_serving_fence(
        _attempt(),
        fence_id="serving-fence-b",
        epoch=12,
        token=OTHER_TOKEN,
        issued_at="2026-07-19T10:02:00Z",
    )


def _acceptance(record: ServingFenceRecord) -> GenerationAcceptance:
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
        entrypoint_proof_sha256="1" * 64,
        database_proof_sha256="2" * 64,
        systemd_proof_sha256="3" * 64,
        index_set_proof_sha256="4" * 64,
        health_proof_sha256="5" * 64,
        accepted_at="2026-07-19T10:03:00Z",
    )


def _state_for_fence(
    record: ServingFenceRecord,
    *,
    mode: GenerationMode,
    maintenance_active: bool,
) -> GenerationState:
    transitioning = mode in (GenerationMode.SWITCHING, GenerationMode.VALIDATING)
    committed = mode is GenerationMode.STEADY and maintenance_active
    control_record, _ = _control()
    return GenerationState(
        schema_version=1,
        state_version=8,
        mode=mode,
        serving_generation_id=(
            record.generation_id if committed or not transitioning else "c" * 64
        ),
        serving_fence_id=(record.fence_id if not transitioning else "serving-fence-a"),
        serving_fence_epoch=(record.epoch if not transitioning else 11),
        serving_fence_token_sha256=(record.token_sha256 if not transitioning else "9" * 64),
        desired_generation_id=(record.generation_id if maintenance_active else None),
        rollback_generation_id="c" * 64,
        control_attempt_id=(record.accepted_attempt_id if maintenance_active else None),
        control_reservation_sha256=(
            control_record.reservation_sha256 if maintenance_active else None
        ),
        control_lease_record_sha256=(
            control_lease_record_sha256(control_record) if maintenance_active else None
        ),
        control_lease_epoch=(4 if maintenance_active else None),
        acceptance_sha256="8" * 64,
        maintenance_active=maintenance_active,
        updated_at="2026-07-19T10:04:00Z",
    )


__all__ = [
    "OTHER_TOKEN",
    "TOKEN",
    "_acceptance",
    "_attempt",
    "_control",
    "_reservation",
    "_serving",
    "_state_for_fence",
]
