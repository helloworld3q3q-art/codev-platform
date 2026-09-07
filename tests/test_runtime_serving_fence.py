"""服务围栏、验收绑定与写入器时序测试。"""

from __future__ import annotations

import dataclasses

import pytest

import codev_platform.runtime_fencing as fencing
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseStatus,
    FencingContractError,
    ServingFenceProof,
    ServingFenceRecord,
    authorize_serving_writer,
    control_lease_record_sha256,
    verify_serving_fence,
)
from codev_platform.runtime_generation_acceptance import serving_binding_sha256
from codev_platform.runtime_generation_acceptance_validation import (
    GenerationAcceptanceBindingError,
    verify_serving_fence_acceptance,
)
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    begin_switch,
    begin_validation,
    commit_serving,
    prepare_serving_publication,
)
from codev_platform.runtime_generation_state_control import (
    GenerationStateControlBindingError,
    verify_active_control_lease_acceptance,
)
from tests.runtime_fencing_test_support import (
    TOKEN,
    _acceptance,
    _attempt,
    _control,
    _serving,
    _state_for_fence,
)


def test_serving_fence绑定attempt_generation并与acceptance语义一致() -> None:
    record, proof = _serving()
    assert record.generation_id == _attempt().target_generation_id
    assert record.accepted_attempt_id == _attempt().attempt_id
    assert verify_serving_fence(record, proof) is proof
    assert verify_serving_fence_acceptance(record, _acceptance(record)) is record

    wrong_acceptance = dataclasses.replace(_acceptance(record), health_proof_sha256="8" * 64)
    # 非 fence 证明变化不影响围栏记录绑定。
    assert verify_serving_fence_acceptance(record, wrong_acceptance) is record
    with pytest.raises(GenerationAcceptanceBindingError, match="acceptance"):
        verify_serving_fence_acceptance(
            record,
            dataclasses.replace(_acceptance(record), serving_fence_epoch=13),
        )


def test_control_lease_acceptance审计归属状态控制域() -> None:
    control_record, _ = _control()
    serving_record, _ = _serving()
    acceptance = _acceptance(serving_record)
    assert not hasattr(fencing, "verify_control_lease_acceptance")
    assert verify_active_control_lease_acceptance(control_record, acceptance) is control_record

    for field, value in (
        ("attempt_id", "b" * 32),
        ("control_lease_epoch_audit", 5),
        ("control_token_sha256_audit", "8" * 64),
        ("control_lease_record_sha256_audit", "8" * 64),
    ):
        with pytest.raises(GenerationStateControlBindingError, match="acceptance"):
            verify_active_control_lease_acceptance(
                control_record,
                dataclasses.replace(acceptance, **{field: value}),
            )


def test_control_lease_acceptance拒绝已退休完整记录() -> None:
    """公开验收只能锚定仍可执行的 ACTIVE control lease。"""
    control_record, _ = _control()
    serving_record, _ = _serving()
    retired = dataclasses.replace(
        control_record,
        status=ControlLeaseStatus.RETIRED,
        terminal_journal_sha256="6" * 64,
        terminal_evidence_sha256="7" * 64,
        retired_at="2026-07-19T10:10:00Z",
        retired_from_sha256=control_lease_record_sha256(control_record),
    )
    acceptance = dataclasses.replace(
        _acceptance(serving_record),
        control_lease_record_sha256_audit=control_lease_record_sha256(retired),
    )

    with pytest.raises(GenerationStateControlBindingError, match="活动"):
        verify_active_control_lease_acceptance(retired, acceptance)


def test_serving_writer并发围栏只在发布后允许当前generation与fence() -> None:
    record, proof = _serving()
    switching = _state_for_fence(
        record,
        mode=GenerationMode.SWITCHING,
        maintenance_active=True,
    )
    committed = _state_for_fence(
        record,
        mode=GenerationMode.STEADY,
        maintenance_active=True,
    )
    published = _state_for_fence(
        record,
        mode=GenerationMode.STEADY,
        maintenance_active=False,
    )

    for closed in (switching, committed):
        with pytest.raises(FencingContractError, match="关闭"):
            authorize_serving_writer(closed, record.generation_id, record, proof)
    assert (
        authorize_serving_writer(
            published,
            record.generation_id,
            record,
            proof,
        )
        is proof
    )
    with pytest.raises(FencingContractError, match="generation"):
        authorize_serving_writer(published, "c" * 64, record, proof)


def test_旧serving_writer在switching即使持有正确旧proof也被拒绝() -> None:
    old_record = ServingFenceRecord(
        schema_version=1,
        fence_id="serving-fence-a",
        generation_id="c" * 64,
        accepted_attempt_id="d" * 32,
        epoch=11,
        token_sha256=ControlLeaseProof(
            attempt_id="a" * 32,
            epoch=4,
            token=TOKEN,
        ).token_sha256,
        issued_at="2026-07-18T10:00:00Z",
    )
    old_proof = ServingFenceProof(fence_id=old_record.fence_id, epoch=11, token=TOKEN)
    switching = _state_for_fence(
        _serving()[0],
        mode=GenerationMode.SWITCHING,
        maintenance_active=True,
    )
    with pytest.raises(FencingContractError, match="关闭"):
        authorize_serving_writer(
            switching,
            old_record.generation_id,
            old_record,
            old_proof,
        )


def test_真实状态转换CAS前后围栏时序() -> None:
    old_proof = ServingFenceProof(fence_id="serving-fence-a", epoch=11, token=TOKEN)
    old_record = ServingFenceRecord(
        schema_version=1,
        fence_id=old_proof.fence_id,
        generation_id=_attempt().baseline_generation_id,
        accepted_attempt_id="d" * 32,
        epoch=old_proof.epoch,
        token_sha256=old_proof.token_sha256,
        issued_at="2026-07-18T10:00:00Z",
    )
    steady = GenerationState(
        schema_version=1,
        state_version=7,
        mode=GenerationMode.STEADY,
        serving_generation_id=old_record.generation_id,
        serving_fence_id=old_record.fence_id,
        serving_fence_epoch=old_record.epoch,
        serving_fence_token_sha256=old_record.token_sha256,
        desired_generation_id=None,
        rollback_generation_id="a" * 64,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256="8" * 64,
        maintenance_active=False,
        updated_at="2026-07-19T10:00:00Z",
    )
    control_record, control_proof = _control()
    target_record, target_proof = _serving()

    assert (
        authorize_serving_writer(
            steady,
            old_record.generation_id,
            old_record,
            old_proof,
        )
        is old_proof
    )
    switching = begin_switch(
        steady,
        _attempt(),
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    for record, proof, generation in (
        (old_record, old_proof, old_record.generation_id),
        (target_record, target_proof, target_record.generation_id),
    ):
        with pytest.raises(FencingContractError, match="关闭"):
            authorize_serving_writer(switching, generation, record, proof)

    validating = begin_validation(
        switching,
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:02:00Z",
    )
    committed = commit_serving(
        validating,
        _acceptance(target_record),
        target_record,
        control_record,
        control_proof,
        control_lease_lineage=(control_record,),
        updated_at="2026-07-19T10:04:00Z",
    )
    with pytest.raises(FencingContractError, match="关闭"):
        authorize_serving_writer(
            committed,
            target_record.generation_id,
            target_record,
            target_proof,
        )
    published = prepare_serving_publication(
        committed,
        serving_binding_sha256(_acceptance(target_record)),
        updated_at="2026-07-19T10:05:00Z",
    )
    assert (
        authorize_serving_writer(
            published,
            target_record.generation_id,
            target_record,
            target_proof,
        )
        is target_proof
    )
