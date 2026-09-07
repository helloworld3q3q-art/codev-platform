"""运行代际 recovery-lineage 与时间边界测试。"""

from __future__ import annotations

import dataclasses

import pytest

from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseStatus,
    ServingFenceProof,
    ServingFenceRecord,
    control_lease_record_sha256,
    recover_control_lease,
)
from codev_platform.runtime_generation_acceptance import serving_binding_sha256
from codev_platform.runtime_generation_state import (
    GenerationStateError,
    begin_switch,
    begin_validation,
    commit_serving,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)
from tests.runtime_generation_state_test_support import (
    OTHER_TOKEN,
    TOKEN,
    _attempt,
    _control,
    _invoke_controlled_transition,
    _lifecycle,
    _reservation,
    _steady,
    _takeover_commit_context,
    _target_acceptance,
    _target_fence,
)


@pytest.mark.parametrize(
    "operation",
    (
        lambda states: begin_switch(
            states[0],
            _attempt(),
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at=states[0].updated_at,
        ),
        lambda states: begin_validation(
            states[1],
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at=states[1].updated_at,
        ),
        lambda states: commit_serving(
            states[2],
            _target_acceptance(),
            _target_fence(),
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at=states[2].updated_at,
        ),
        lambda states: prepare_serving_publication(
            states[3],
            states[3].acceptance_sha256,
            updated_at=states[3].updated_at,
        ),
        lambda states: mark_restricted(
            states[2],
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at=states[2].updated_at,
        ),
        lambda states: mark_safety_unproven(
            states[2],
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at=states[2].updated_at,
        ),
    ),
)
def test_generation_state所有转换拒绝非递增updated_at(operation) -> None:
    with pytest.raises(GenerationStateError):
        operation(_lifecycle())


def test_commit_serving拒绝同epoch但control_token审计不匹配当前lease() -> None:
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
    mismatched_acceptance = dataclasses.replace(
        _target_acceptance(fence),
        control_lease_epoch_audit=record.epoch,
        control_token_sha256_audit="9" * 64,
    )

    with pytest.raises(GenerationStateError):
        commit_serving(
            validating,
            mismatched_acceptance,
            fence,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:04:00Z",
        )


def test_commit_serving拒绝同epoch_token但control_record摘要不匹配() -> None:
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
    mismatched_acceptance = dataclasses.replace(
        _target_acceptance(fence),
        control_lease_record_sha256_audit="9" * 64,
    )

    with pytest.raises(GenerationStateError):
        commit_serving(
            validating,
            mismatched_acceptance,
            fence,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:04:00Z",
        )


def test_recovery显式lineage缺少predecessor时拒绝收敛() -> None:
    validating, acceptance, fence, _, recovered_record, recovered_proof = _takeover_commit_context()

    with pytest.raises(GenerationStateError):
        commit_serving(
            validating,
            acceptance,
            fence,
            recovered_record,
            recovered_proof,
            control_lease_lineage=(recovered_record,),
            updated_at="2026-07-19T10:04:00Z",
        )


def test_recovery拒绝篡改完整predecessor记录的lineage() -> None:
    validating, acceptance, fence, record, recovered_record, recovered_proof = (
        _takeover_commit_context()
    )
    tampered_predecessor = dataclasses.replace(record, owner="attacker")

    with pytest.raises(GenerationStateError):
        commit_serving(
            validating,
            acceptance,
            fence,
            recovered_record,
            recovered_proof,
            updated_at="2026-07-19T10:04:00Z",
            control_lease_lineage=(tampered_predecessor, recovered_record),
        )


def test_recovery提升epoch轮换token后仍可用真实旧acceptance收敛() -> None:
    validating, acceptance, fence, record, recovered_record, recovered_proof = (
        _takeover_commit_context()
    )
    committed = commit_serving(
        validating,
        acceptance,
        fence,
        recovered_record,
        recovered_proof,
        updated_at="2026-07-19T10:04:00Z",
        control_lease_lineage=(record, recovered_record),
    )

    assert committed.control_lease_epoch == recovered_record.epoch
    assert committed.control_lease_record_sha256 == control_lease_record_sha256(recovered_record)
    assert committed.acceptance_sha256 == serving_binding_sha256(acceptance)


def test_recovery后拒绝伪称由旧lease产生的新acceptance() -> None:
    """旧 lease 的验收只能发生在其生效和后继接管之间。"""
    validating, acceptance, fence, record, recovered_record, recovered_proof = (
        _takeover_commit_context()
    )
    late_acceptance = dataclasses.replace(
        acceptance,
        accepted_at="2026-07-19T10:03:50Z",
    )

    with pytest.raises(GenerationStateError, match="后继|lineage"):
        commit_serving(
            validating,
            late_acceptance,
            fence,
            recovered_record,
            recovered_proof,
            updated_at="2026-07-19T10:04:00Z",
            control_lease_lineage=(record, recovered_record),
        )


def test后继接管早于已绑定状态时间时拒绝继续推进() -> None:
    """旧锚点状态不能在后继 control lease 已接管后继续写入。"""
    record, proof = _control()
    switching = begin_switch(
        _steady(),
        _attempt(),
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=13,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:00:45Z",
    )

    with pytest.raises(GenerationStateError, match="后继|lineage"):
        begin_validation(
            switching,
            recovered_record,
            recovered_proof,
            updated_at="2026-07-19T10:02:00Z",
            control_lease_lineage=(record, recovered_record),
        )


def test提交时间必须晚于验收围栏与当前control_lease() -> None:
    validating, acceptance, fence, _, _, _ = _takeover_commit_context()
    record, proof = _control()

    with pytest.raises(GenerationStateError, match="updated_at"):
        commit_serving(
            validating,
            acceptance,
            fence,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:03:15Z",
        )


@pytest.mark.parametrize(
    "fence",
    (
        _target_fence(generation_id="6" * 64),
        _target_fence(accepted_attempt_id="e" * 32),
        _target_fence(epoch=11),
        _target_fence(fence_id="serving-fence-a"),
    ),
)
def test_commit_serving拒绝错误或非新围栏(fence: ServingFenceRecord) -> None:
    with pytest.raises(GenerationStateError, match="[Ff]ence"):
        commit_serving(
            _lifecycle()[2],
            _target_acceptance(),
            fence,
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at="2026-07-19T10:06:00Z",
        )


@pytest.mark.parametrize(
    "entry",
    (
        "begin_switch",
        "begin_validation",
        "commit_serving",
        "mark_restricted",
        "mark_safety_unproven",
    ),
)
def test_state每个控制写入口都拒绝未锚定或非活动lease(entry: str) -> None:
    record, proof = _control()
    retired = dataclasses.replace(
        record,
        status=ControlLeaseStatus.RETIRED,
        terminal_journal_sha256="6" * 64,
        terminal_evidence_sha256="7" * 64,
        retired_at="2026-07-19T10:10:00Z",
        retired_from_sha256=control_lease_record_sha256(record),
    )
    invalid_pairs = (
        (record, ControlLeaseProof(proof.attempt_id, proof.epoch, OTHER_TOKEN)),
        (retired, proof),
        (record, ServingFenceProof("serving-fence-x", proof.epoch, TOKEN)),
    )
    for invalid_record, invalid_proof in invalid_pairs:
        with pytest.raises(GenerationStateError):
            _invoke_controlled_transition(entry, invalid_record, invalid_proof)
