"""运行代际状态边及其 control lease 绑定校验。"""

from __future__ import annotations

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_strictly_later,
)
from codev_platform.runtime_attempt_contract import (
    DeploymentAttempt,
    deployment_attempt_reservation_sha256,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ServingFenceRecord,
    control_lease_record_sha256,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    serving_binding_sha256,
)
from codev_platform.runtime_generation_acceptance_validation import (
    GenerationAcceptanceBindingError,
    verify_serving_fence_acceptance,
)
from codev_platform.runtime_generation_state_model import (
    _MAX_VERSION,
    GenerationMode,
    GenerationState,
    GenerationStateError,
)
from codev_platform.runtime_generation_state_control import (
    GenerationStateControlBindingError,
    require_acceptance_control_lease_lineage,
    require_bound_control_lease,
    require_unbound_control_lease,
)


def begin_switch(
    current: GenerationState,
    attempt: DeploymentAttempt,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    """绑定 desired/rollback/attempt，并进入 switching。"""
    _require_state(current)
    _require_transition_time(current, updated_at)
    if current.mode is not GenerationMode.STEADY or current.maintenance_active:
        raise GenerationStateError("begin_switch 状态边只接受公开 steady")
    if type(attempt) is not DeploymentAttempt:
        raise GenerationStateError("只接受 DeploymentAttempt")
    _require_unbound_lease(
        lease_record,
        lease,
        control_lease_lineage=control_lease_lineage,
        event_at=updated_at,
    )
    if deployment_attempt_reservation_sha256(attempt) != lease_record.reservation_sha256:
        raise GenerationStateError("DeploymentAttempt 与 control lease reservation 不一致")
    if attempt.baseline_generation_id != current.serving_generation_id:
        raise GenerationStateError("attempt baseline 与当前 serving generation 不一致")
    return GenerationState(
        schema_version=current.schema_version,
        state_version=_next_version(current),
        mode=GenerationMode.SWITCHING,
        serving_generation_id=current.serving_generation_id,
        serving_fence_id=current.serving_fence_id,
        serving_fence_epoch=current.serving_fence_epoch,
        serving_fence_token_sha256=current.serving_fence_token_sha256,
        desired_generation_id=attempt.target_generation_id,
        rollback_generation_id=current.serving_generation_id,
        control_attempt_id=attempt.attempt_id,
        control_reservation_sha256=lease_record.reservation_sha256,
        control_lease_record_sha256=control_lease_record_sha256(lease_record),
        control_lease_epoch=lease_record.epoch,
        acceptance_sha256=current.acceptance_sha256,
        maintenance_active=True,
        updated_at=updated_at,
    )


def begin_validation(
    current: GenerationState,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    """资源切换完毕后进入 validating。"""
    _require_state(current)
    _require_transition_time(current, updated_at)
    if current.mode is not GenerationMode.SWITCHING:
        raise GenerationStateError("begin_validation 状态边只接受 switching")
    _require_bound_lease(
        current,
        lease_record,
        lease,
        control_lease_lineage=control_lease_lineage,
        state_updated_at=current.updated_at,
    )
    _require_transition_time(
        current,
        updated_at,
        boundaries=((lease_record.issued_at, "当前 control lease issued_at"),),
    )
    return GenerationState(
        schema_version=current.schema_version,
        state_version=_next_version(current),
        mode=GenerationMode.VALIDATING,
        serving_generation_id=current.serving_generation_id,
        serving_fence_id=current.serving_fence_id,
        serving_fence_epoch=current.serving_fence_epoch,
        serving_fence_token_sha256=current.serving_fence_token_sha256,
        desired_generation_id=current.desired_generation_id,
        rollback_generation_id=current.rollback_generation_id,
        control_attempt_id=current.control_attempt_id,
        control_reservation_sha256=current.control_reservation_sha256,
        control_lease_record_sha256=control_lease_record_sha256(lease_record),
        control_lease_epoch=lease_record.epoch,
        acceptance_sha256=current.acceptance_sha256,
        maintenance_active=True,
        updated_at=updated_at,
    )


def commit_serving(
    current: GenerationState,
    acceptance: GenerationAcceptance,
    serving_fence: ServingFenceRecord,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    """只在完整验收后提交新 serving fence，并保持维护门禁关闭。"""
    _require_state(current)
    if current.mode is not GenerationMode.VALIDATING:
        raise GenerationStateError("commit_serving 状态边只接受 validating")
    lineage = _require_bound_lease(
        current,
        lease_record,
        lease,
        control_lease_lineage=control_lease_lineage,
        state_updated_at=current.updated_at,
    )
    if type(acceptance) is not GenerationAcceptance:
        raise GenerationStateError("只接受 GenerationAcceptance")
    if type(serving_fence) is not ServingFenceRecord:
        raise GenerationStateError("只接受 ServingFenceRecord")
    _require_acceptance_lineage(
        acceptance,
        reservation_sha256=current.control_reservation_sha256,
        current_lease=lease_record,
        control_lease_lineage=lineage,
    )
    try:
        verify_serving_fence_acceptance(serving_fence, acceptance)
    except GenerationAcceptanceBindingError as exc:
        raise GenerationStateError(str(exc)) from None
    fence_matches = (
        serving_fence.generation_id == current.desired_generation_id
        and serving_fence.accepted_attempt_id == current.control_attempt_id
        and serving_fence.epoch > current.serving_fence_epoch
        and serving_fence.fence_id != current.serving_fence_id
    )
    if not fence_matches:
        raise GenerationStateError("serving fence 与待提交代际或旧围栏不一致")
    _require_transition_time(
        current,
        updated_at,
        boundaries=(
            (lease_record.issued_at, "当前 control lease issued_at"),
            (acceptance.accepted_at, "acceptance accepted_at"),
            (serving_fence.issued_at, "ServingFence issued_at"),
        ),
    )
    return GenerationState(
        schema_version=current.schema_version,
        state_version=_next_version(current),
        mode=GenerationMode.STEADY,
        serving_generation_id=serving_fence.generation_id,
        serving_fence_id=serving_fence.fence_id,
        serving_fence_epoch=serving_fence.epoch,
        serving_fence_token_sha256=serving_fence.token_sha256,
        desired_generation_id=serving_fence.generation_id,
        rollback_generation_id=current.rollback_generation_id,
        control_attempt_id=current.control_attempt_id,
        control_reservation_sha256=current.control_reservation_sha256,
        control_lease_record_sha256=control_lease_record_sha256(lease_record),
        control_lease_epoch=lease_record.epoch,
        acceptance_sha256=serving_binding_sha256(acceptance),
        maintenance_active=True,
        updated_at=updated_at,
    )


def prepare_serving_publication(
    current: GenerationState,
    acceptance_sha256: str,
    *,
    updated_at: str,
) -> GenerationState:
    """纯计算维护门禁关闭后的目标状态 B，供 permit 预绑定和后续 CAS。"""
    _require_state(current)
    _require_transition_time(current, updated_at)
    if current.mode is not GenerationMode.STEADY or not current.maintenance_active:
        raise GenerationStateError("prepare_serving_publication 状态边只接受待发布 steady")
    if acceptance_sha256 != current.acceptance_sha256:
        raise GenerationStateError("待发布 acceptance 摘要与 state 不一致")
    return GenerationState(
        schema_version=current.schema_version,
        state_version=_next_version(current),
        mode=GenerationMode.STEADY,
        serving_generation_id=current.serving_generation_id,
        serving_fence_id=current.serving_fence_id,
        serving_fence_epoch=current.serving_fence_epoch,
        serving_fence_token_sha256=current.serving_fence_token_sha256,
        desired_generation_id=None,
        rollback_generation_id=current.rollback_generation_id,
        control_attempt_id=None,
        control_reservation_sha256=None,
        control_lease_record_sha256=None,
        control_lease_epoch=None,
        acceptance_sha256=current.acceptance_sha256,
        maintenance_active=False,
        updated_at=updated_at,
    )


def mark_restricted(
    current: GenerationState,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    """把可证明但不可继续自动推进的分支收敛为受限态。"""
    _require_state(current)
    if current.mode is GenerationMode.SAFETY_UNPROVEN:
        raise GenerationStateError("safety_unproven 是不可退出的安全终态")
    if current.mode is GenerationMode.RESTRICTED:
        raise GenerationStateError("mark_restricted 状态边不接受 restricted")
    return _mark_hazard(
        current,
        GenerationMode.RESTRICTED,
        lease_record,
        lease,
        control_lease_lineage=control_lease_lineage,
        updated_at=updated_at,
    )


def mark_safety_unproven(
    current: GenerationState,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    """无法证明补偿结果时关闭服务许可。"""
    _require_state(current)
    if current.mode is GenerationMode.SAFETY_UNPROVEN:
        raise GenerationStateError("safety_unproven 是不可退出的安全终态")
    return _mark_hazard(
        current,
        GenerationMode.SAFETY_UNPROVEN,
        lease_record,
        lease,
        control_lease_lineage=control_lease_lineage,
        updated_at=updated_at,
    )


def _mark_hazard(
    current: GenerationState,
    target: GenerationMode,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    updated_at: str,
) -> GenerationState:
    _require_state(current)
    _require_transition_time(current, updated_at)
    if current.control_attempt_id is None:
        _require_unbound_lease(
            lease_record,
            lease,
            control_lease_lineage=control_lease_lineage,
            event_at=updated_at,
        )
        control_attempt_id = lease_record.attempt_id
        control_reservation_sha256 = lease_record.reservation_sha256
    else:
        _require_bound_lease(
            current,
            lease_record,
            lease,
            control_lease_lineage=control_lease_lineage,
            state_updated_at=current.updated_at,
        )
        _require_transition_time(
            current,
            updated_at,
            boundaries=((lease_record.issued_at, "当前 control lease issued_at"),),
        )
        control_attempt_id = current.control_attempt_id
        control_reservation_sha256 = current.control_reservation_sha256
    return GenerationState(
        schema_version=current.schema_version,
        state_version=_next_version(current),
        mode=target,
        serving_generation_id=current.serving_generation_id,
        serving_fence_id=current.serving_fence_id,
        serving_fence_epoch=current.serving_fence_epoch,
        serving_fence_token_sha256=current.serving_fence_token_sha256,
        desired_generation_id=current.desired_generation_id,
        rollback_generation_id=current.rollback_generation_id,
        control_attempt_id=control_attempt_id,
        control_reservation_sha256=control_reservation_sha256,
        control_lease_record_sha256=control_lease_record_sha256(lease_record),
        control_lease_epoch=lease_record.epoch,
        acceptance_sha256=current.acceptance_sha256,
        maintenance_active=True,
        updated_at=updated_at,
    )


def _require_state(value: object) -> None:
    if type(value) is not GenerationState:
        raise GenerationStateError("只接受 GenerationState")


def _require_bound_lease(
    current: GenerationState,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    state_updated_at: str,
) -> tuple[ControlLeaseRecord, ...]:
    try:
        return require_bound_control_lease(
            control_attempt_id=current.control_attempt_id,
            control_reservation_sha256=current.control_reservation_sha256,
            control_lease_record_sha256_audit=current.control_lease_record_sha256,
            control_lease_epoch=current.control_lease_epoch,
            lease_record=lease_record,
            lease=lease,
            control_lease_lineage=control_lease_lineage,
            state_updated_at=state_updated_at,
        )
    except GenerationStateControlBindingError as exc:
        raise GenerationStateError(str(exc)) from None


def _require_unbound_lease(
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    event_at: str,
) -> tuple[ControlLeaseRecord, ...]:
    try:
        return require_unbound_control_lease(
            lease_record,
            lease,
            control_lease_lineage=control_lease_lineage,
            event_at=event_at,
        )
    except GenerationStateControlBindingError as exc:
        raise GenerationStateError(str(exc)) from None


def _require_acceptance_lineage(
    acceptance: GenerationAcceptance,
    *,
    reservation_sha256: str | None,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    if reservation_sha256 is None:
        raise GenerationStateError("当前状态未绑定 control lease reservation")
    try:
        require_acceptance_control_lease_lineage(
            acceptance,
            reservation_sha256=reservation_sha256,
            current_lease=current_lease,
            control_lease_lineage=control_lease_lineage,
        )
    except GenerationStateControlBindingError as exc:
        raise GenerationStateError(str(exc)) from None


def _next_version(current: GenerationState) -> int:
    if current.state_version >= _MAX_VERSION:
        raise GenerationStateError("state_version 无法继续提升")
    return current.state_version + 1


def _require_transition_time(
    current: GenerationState,
    updated_at: object,
    *,
    boundaries: tuple[tuple[str, str], ...] = (),
) -> None:
    try:
        require_strictly_later(
            updated_at,
            current.updated_at,
            field="updated_at",
            boundary_field="当前 state updated_at",
        )
    except RuntimeContractSupportError as exc:
        raise GenerationStateError(str(exc)) from None
    for boundary, boundary_field in boundaries:
        try:
            require_strictly_later(
                updated_at,
                boundary,
                field="updated_at",
                boundary_field=boundary_field,
            )
        except RuntimeContractSupportError as exc:
            raise GenerationStateError(str(exc)) from None


__all__ = [
    "begin_switch",
    "begin_validation",
    "commit_serving",
    "mark_restricted",
    "mark_safety_unproven",
    "prepare_serving_publication",
]
