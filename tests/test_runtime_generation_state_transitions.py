"""运行代际状态转换与危险态测试。"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from codev_platform.runtime_fencing import (
    ServingFenceProof,
    control_lease_record_sha256,
    issue_control_lease,
    recover_control_lease,
)
from codev_platform.runtime_generation_acceptance import serving_binding_sha256
from codev_platform.runtime_generation_state import (
    GenerationMode,
    GenerationState,
    GenerationStateError,
    begin_switch,
    begin_validation,
    commit_serving,
    generation_state_sha256,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)
from tests.runtime_generation_state_test_support import (
    OTHER_TOKEN,
    TOKEN,
    _attempt,
    _control,
    _lifecycle,
    _reservation,
    _steady,
    _target_acceptance,
    _target_fence,
)


def test_generation_state完整显式状态图与维护门禁() -> None:
    steady, switching, validating, committed, published = _lifecycle()
    assert tuple(state.mode for state in (steady, switching, validating, committed)) == (
        GenerationMode.STEADY,
        GenerationMode.SWITCHING,
        GenerationMode.VALIDATING,
        GenerationMode.STEADY,
    )
    assert tuple(state.state_version for state in _lifecycle()) == (7, 8, 9, 10, 11)
    assert tuple(state.maintenance_active for state in _lifecycle()) == (
        False,
        True,
        True,
        True,
        False,
    )
    assert switching.serving_generation_id == steady.serving_generation_id
    assert switching.desired_generation_id == _attempt().target_generation_id
    assert switching.rollback_generation_id == steady.serving_generation_id
    assert switching.control_attempt_id == _attempt().attempt_id
    control_record, _ = _control()
    control_record_sha256 = control_lease_record_sha256(control_record)
    assert switching.control_lease_record_sha256 == control_record_sha256
    assert validating.control_lease_record_sha256 == control_record_sha256
    assert committed.control_lease_record_sha256 == control_record_sha256
    assert committed.serving_generation_id == _attempt().target_generation_id
    assert committed.serving_fence_id == _target_fence().fence_id
    assert committed.acceptance_sha256 == serving_binding_sha256(_target_acceptance())
    assert published.desired_generation_id is None
    assert published.rollback_generation_id == committed.rollback_generation_id
    assert published.control_attempt_id is None
    assert published.control_reservation_sha256 is None
    assert published.control_lease_record_sha256 is None
    assert published.control_lease_epoch is None


def test_generation_state每条边只能改变声明字段且无任意kwargs入口() -> None:
    states = _lifecycle()
    expected_changes = (
        {
            "state_version",
            "mode",
            "desired_generation_id",
            "rollback_generation_id",
            "control_attempt_id",
            "control_reservation_sha256",
            "control_lease_record_sha256",
            "control_lease_epoch",
            "maintenance_active",
            "updated_at",
        },
        {"state_version", "mode", "updated_at"},
        {
            "state_version",
            "mode",
            "serving_generation_id",
            "serving_fence_id",
            "serving_fence_epoch",
            "serving_fence_token_sha256",
            "acceptance_sha256",
            "updated_at",
        },
        {
            "state_version",
            "desired_generation_id",
            "control_attempt_id",
            "control_reservation_sha256",
            "control_lease_record_sha256",
            "control_lease_epoch",
            "maintenance_active",
            "updated_at",
        },
    )
    fields = tuple(field.name for field in dataclasses.fields(GenerationState))
    for before, after, expected in zip(
        states[:-1],
        states[1:],
        expected_changes,
        strict=True,
    ):
        changed = {field for field in fields if getattr(before, field) != getattr(after, field)}
        assert changed == expected
    for transition in (
        begin_switch,
        begin_validation,
        commit_serving,
        prepare_serving_publication,
        mark_restricted,
        mark_safety_unproven,
    ):
        assert all(
            parameter.kind is not inspect.Parameter.VAR_KEYWORD
            for parameter in inspect.signature(transition).parameters.values()
        )


def test_prepare_serving_publication是确定性纯转换并绑定acceptance() -> None:
    committed = _lifecycle()[3]
    acceptance_sha256 = committed.acceptance_sha256
    first = prepare_serving_publication(
        committed,
        acceptance_sha256,
        updated_at="2026-07-19T10:05:00Z",
    )
    second = prepare_serving_publication(
        committed,
        acceptance_sha256,
        updated_at="2026-07-19T10:05:00Z",
    )
    assert first == second
    assert generation_state_sha256(first) == generation_state_sha256(second)
    with pytest.raises(GenerationStateError, match="acceptance"):
        prepare_serving_publication(
            committed,
            "5" * 64,
            updated_at="2026-07-19T10:05:00Z",
        )


@pytest.mark.parametrize("source_index", (0, 1, 2, 3))
@pytest.mark.parametrize(
    ("transition", "target_mode"),
    (
        (mark_restricted, GenerationMode.RESTRICTED),
        (mark_safety_unproven, GenerationMode.SAFETY_UNPROVEN),
    ),
)
def test_任意危险分支只能经专责边关闭服务许可(
    source_index: int,
    transition,
    target_mode: GenerationMode,
) -> None:
    source = _lifecycle()[source_index]
    record, proof = _control()
    changed = transition(
        source,
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:06:00Z",
    )
    assert changed.mode is target_mode
    assert changed.maintenance_active is True
    assert changed.state_version == source.state_version + 1
    assert changed.serving_generation_id == source.serving_generation_id
    assert changed.serving_fence_id == source.serving_fence_id


def test_hazard转换绑定当前完整control_lease_record摘要() -> None:
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
        issued_at="2026-07-19T10:01:30Z",
    )
    restricted = mark_restricted(
        switching,
        recovered_record,
        recovered_proof,
        updated_at="2026-07-19T10:02:00Z",
        control_lease_lineage=(record, recovered_record),
    )

    assert restricted.control_lease_record_sha256 == control_lease_record_sha256(recovered_record)


def test_restricted可以升级为safety_unproven但安全未证明不可退出() -> None:
    record, proof = _control()
    restricted = mark_restricted(
        _lifecycle()[2],
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:06:00Z",
    )
    unsafe = mark_safety_unproven(
        restricted,
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:07:00Z",
    )
    assert unsafe.mode is GenerationMode.SAFETY_UNPROVEN
    with pytest.raises(GenerationStateError, match="终态"):
        mark_restricted(
            unsafe,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:08:00Z",
        )


@pytest.mark.parametrize("transition", (mark_restricted, mark_safety_unproven))
def test_危险态专责边对错误state与proof统一fail_closed(transition) -> None:
    record, proof = _control()
    with pytest.raises(GenerationStateError, match="GenerationState"):
        transition(
            object(),  # type: ignore[arg-type]
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:06:00Z",
        )
    record, _ = _control()
    with pytest.raises(GenerationStateError, match="ControlLeaseProof"):
        transition(
            _steady(),
            record,
            ServingFenceProof(fence_id="fence-x", epoch=12, token=TOKEN),  # type: ignore[arg-type]
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:06:00Z",
        )


@pytest.mark.parametrize(
    "operation",
    (
        lambda states: begin_switch(
            states[1],
            _attempt(),
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at="2026-07-19T10:06:00Z",
        ),
        lambda states: begin_validation(
            states[0],
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at="2026-07-19T10:06:00Z",
        ),
        lambda states: commit_serving(
            states[1],
            _target_acceptance(),
            _target_fence(),
            *_control(),
            control_lease_lineage=(_control()[0],),
            updated_at="2026-07-19T10:06:00Z",
        ),
        lambda states: prepare_serving_publication(
            states[2],
            "4" * 64,
            updated_at="2026-07-19T10:06:00Z",
        ),
    ),
)
def test_generation_state拒绝所有未声明状态边(operation) -> None:
    with pytest.raises(GenerationStateError, match="状态边"):
        operation(_lifecycle())


def test_generation_state转换拒绝错误attempt旧epoch与proof类型() -> None:
    steady, switching, validating, _, _ = _lifecycle()
    baseline_record, baseline_proof = _control()
    with pytest.raises(GenerationStateError, match="baseline"):
        begin_switch(
            steady,
            _attempt(baseline_generation_id="6" * 64),
            baseline_record,
            baseline_proof,
            control_lease_lineage=(baseline_record,),
            updated_at="2026-07-19T10:06:00Z",
        )
    other_record, other_proof = _control(attempt_id="e" * 32)
    with pytest.raises(GenerationStateError, match="attempt"):
        begin_validation(
            switching,
            other_record,
            other_proof,
            control_lease_lineage=(other_record,),
            updated_at="2026-07-19T10:06:00Z",
        )
    record, proof = _control()
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=13,
        token=OTHER_TOKEN,
        owner="recovery-a",
        issued_at="2026-07-19T10:05:30Z",
    )
    recovered = begin_validation(
        switching,
        recovered_record,
        recovered_proof,
        updated_at="2026-07-19T10:06:00Z",
        control_lease_lineage=(record, recovered_record),
    )
    assert recovered.control_lease_epoch == 13
    assert recovered.control_lease_record_sha256 == control_lease_record_sha256(recovered_record)
    with pytest.raises(GenerationStateError):
        commit_serving(
            recovered,
            _target_acceptance(),
            _target_fence(),
            record,
            proof,
            control_lease_lineage=(record, recovered_record),
            updated_at="2026-07-19T10:07:00Z",
        )
    record, _ = _control()
    with pytest.raises(GenerationStateError, match="ControlLeaseProof"):
        begin_validation(
            switching,
            record,
            ServingFenceProof(fence_id="fence-x", epoch=12, token=TOKEN),  # type: ignore[arg-type]
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:06:00Z",
        )
    assert validating.control_lease_epoch == 12


def test_begin_switch拒绝同attempt_id但reservation字段漂移() -> None:
    record, proof = _control()
    with pytest.raises(GenerationStateError):
        begin_switch(
            _steady(),
            _attempt(plan_sha256="9" * 64),
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:01:00Z",
        )


def test_bound_state拒绝同attempt_id但不同reservation的后续lease() -> None:
    original_record, original_proof = _control()
    switching = begin_switch(
        _steady(),
        _attempt(),
        original_record,
        original_proof,
        control_lease_lineage=(original_record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    drifted_reservation = dataclasses.replace(
        _reservation(),
        plan_sha256="9" * 64,
    )
    record, proof = issue_control_lease(
        drifted_reservation,
        epoch=12,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )

    with pytest.raises(GenerationStateError, match="reservation"):
        begin_validation(
            switching,
            record,
            proof,
            control_lease_lineage=(record,),
            updated_at="2026-07-19T10:02:00Z",
        )


def test_bound_state拒绝同epoch_token但完整record摘要漂移() -> None:
    record, proof = _control()
    switching = begin_switch(
        _steady(),
        _attempt(),
        record,
        proof,
        control_lease_lineage=(record,),
        updated_at="2026-07-19T10:01:00Z",
    )
    tampered_record = dataclasses.replace(record, owner="attacker")

    with pytest.raises(GenerationStateError):
        begin_validation(
            switching,
            tampered_record,
            proof,
            control_lease_lineage=(tampered_record,),
            updated_at="2026-07-19T10:02:00Z",
        )
