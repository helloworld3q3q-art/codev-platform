"""ServingPermit 与公开稳态绑定契约测试。"""

from __future__ import annotations

import importlib

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    freeze_deployment_attempt,
)
from codev_platform.runtime_fencing import (
    ServingFenceRecord,
    control_lease_record_sha256,
    issue_control_lease,
    issue_serving_fence,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    commit_serving,
    generation_state_sha256,
)


def _serving_context() -> tuple[
    GenerationState,
    GenerationAcceptance,
    ServingFenceRecord,
]:
    reservation = AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="e" * 64,
        controller_sha256="f" * 64,
        created_at="2026-07-19T10:00:00Z",
    )
    attempt = freeze_deployment_attempt(
        reservation,
        target_generation_id="b" * 64,
        baseline_generation_id="c" * 64,
        baseline_observation_sha256="d" * 64,
    )
    fence, _ = issue_serving_fence(
        attempt,
        fence_id="fence-b",
        epoch=2,
        token=bytes(range(32)),
        issued_at="2026-07-19T10:01:00Z",
    )
    control_record, _ = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )
    acceptance = GenerationAcceptance(
        schema_version=1,
        attempt_id=attempt.attempt_id,
        generation_id=attempt.target_generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        control_lease_epoch_audit=control_record.epoch,
        control_token_sha256_audit=control_record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(control_record),
        entrypoint_proof_sha256="2" * 64,
        database_proof_sha256="3" * 64,
        systemd_proof_sha256="4" * 64,
        index_set_proof_sha256="5" * 64,
        health_proof_sha256="6" * 64,
        accepted_at="2026-07-19T10:02:00Z",
    )
    state = GenerationState(
        schema_version=1,
        state_version=4,
        mode=GenerationMode.STEADY,
        serving_generation_id=attempt.target_generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        desired_generation_id=None,
        rollback_generation_id=attempt.baseline_generation_id,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256=serving_binding_sha256(acceptance),
        maintenance_active=False,
        updated_at="2026-07-19T10:03:00Z",
    )
    return state, acceptance, fence


def test_serving_permit只从相互一致的typed公开稳态创建() -> None:
    permit_contract = importlib.import_module("codev_platform.runtime_serving_permit")
    state, acceptance, fence = _serving_context()

    permit = permit_contract.create_serving_permit(
        state,
        acceptance,
        fence,
        issued_at="2026-07-19T10:04:00Z",
    )

    assert permit.generation_state_sha256 == generation_state_sha256(state)
    assert permit.acceptance_sha256 == serving_binding_sha256(acceptance)
    assert permit_contract.verify_serving_permit(permit, state, acceptance, fence) is permit


def test_commit_serving只接受与fence一致的typed_acceptance() -> None:
    reservation = AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="e" * 64,
        controller_sha256="f" * 64,
        created_at="2026-07-19T10:00:00Z",
    )
    attempt = freeze_deployment_attempt(
        reservation,
        target_generation_id="b" * 64,
        baseline_generation_id="c" * 64,
        baseline_observation_sha256="d" * 64,
    )
    lease_record, lease = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:00Z",
    )
    validating = GenerationState(
        schema_version=1,
        state_version=3,
        mode=GenerationMode.VALIDATING,
        serving_generation_id=attempt.baseline_generation_id,
        serving_fence_id="fence-a",
        serving_fence_epoch=1,
        serving_fence_token_sha256="7" * 64,
        desired_generation_id=attempt.target_generation_id,
        rollback_generation_id=attempt.baseline_generation_id,
        control_attempt_id=attempt.attempt_id,
        control_reservation_sha256=lease_record.reservation_sha256,
        control_lease_record_sha256=control_lease_record_sha256(lease_record),
        control_lease_epoch=lease_record.epoch,
        acceptance_sha256="8" * 64,
        maintenance_active=True,
        updated_at="2026-07-19T10:02:00Z",
    )
    fence, _ = issue_serving_fence(
        attempt,
        fence_id="fence-b",
        epoch=2,
        token=bytes(range(32, 64)),
        issued_at="2026-07-19T10:03:00Z",
    )
    acceptance = GenerationAcceptance(
        schema_version=1,
        attempt_id=attempt.attempt_id,
        generation_id=attempt.target_generation_id,
        serving_fence_id=fence.fence_id,
        serving_fence_epoch=fence.epoch,
        serving_fence_token_sha256=fence.token_sha256,
        control_lease_epoch_audit=lease_record.epoch,
        control_token_sha256_audit=lease_record.token_sha256,
        control_lease_record_sha256_audit=control_lease_record_sha256(lease_record),
        entrypoint_proof_sha256="2" * 64,
        database_proof_sha256="3" * 64,
        systemd_proof_sha256="4" * 64,
        index_set_proof_sha256="5" * 64,
        health_proof_sha256="6" * 64,
        accepted_at="2026-07-19T10:04:00Z",
    )

    committed = commit_serving(
        validating,
        acceptance,
        fence,
        lease_record,
        lease,
        control_lease_lineage=(lease_record,),
        updated_at="2026-07-19T10:05:00Z",
    )

    assert committed.acceptance_sha256 == serving_binding_sha256(acceptance)


def test_serving_fence持久记录有显式schema() -> None:
    _, _, fence = _serving_context()

    assert fence.schema_version == 1


def test_serving_permit严格codec往返() -> None:
    permit_contract = importlib.import_module("codev_platform.runtime_serving_permit")
    state, acceptance, fence = _serving_context()
    permit = permit_contract.create_serving_permit(
        state,
        acceptance,
        fence,
        issued_at="2026-07-19T10:04:00Z",
    )

    payload = permit_contract.encode_serving_permit(permit)

    assert permit_contract.decode_serving_permit(payload) == permit


def test_serving_permit_codec拒绝重复字段与额外字段() -> None:
    permit_contract = importlib.import_module("codev_platform.runtime_serving_permit")
    state, acceptance, fence = _serving_context()
    permit = permit_contract.create_serving_permit(
        state,
        acceptance,
        fence,
        issued_at="2026-07-19T10:04:00Z",
    )
    payload = permit_contract.encode_serving_permit(permit)
    duplicate = payload.replace(
        b'{"acceptance_sha256":',
        b'{"schema_version":1,"acceptance_sha256":',
        1,
    )
    extra = payload[:-1] + b',"unexpected":true}'

    with pytest.raises(permit_contract.ServingPermitError):
        permit_contract.decode_serving_permit(duplicate)
    with pytest.raises(permit_contract.ServingPermitError):
        permit_contract.decode_serving_permit(extra)
