"""运行代际状态边的 control lease capability 与 lineage 绑定。"""

from __future__ import annotations

import hmac

from codev_platform.runtime_control_lease_lineage import (
    ControlLeaseAnchor,
    ControlLeaseLineageError,
    verify_anchor_event_window,
    verify_current_active_lineage,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    FencingContractError,
    control_lease_record_sha256,
    verify_control_lease,
)
from codev_platform.runtime_generation_acceptance import GenerationAcceptance


class GenerationStateControlBindingError(ValueError):
    """状态边无法证明当前 control lease 或其审计 lineage。"""


def verify_active_control_lease_acceptance(
    record: ControlLeaseRecord,
    acceptance: GenerationAcceptance,
) -> ControlLeaseRecord:
    """复验 acceptance 只锚定完整且仍处于 ACTIVE 的控制租约。"""
    if type(record) is not ControlLeaseRecord:
        raise GenerationStateControlBindingError("只接受 ControlLeaseRecord")
    if type(acceptance) is not GenerationAcceptance:
        raise GenerationStateControlBindingError("只接受 GenerationAcceptance")
    if record.status is not ControlLeaseStatus.ACTIVE:
        raise GenerationStateControlBindingError("control lease 不是活动状态")
    scalars_match = (
        record.attempt_id == acceptance.attempt_id
        and record.epoch == acceptance.control_lease_epoch_audit
    )
    token_matches = hmac.compare_digest(
        record.token_sha256,
        acceptance.control_token_sha256_audit,
    )
    record_matches = hmac.compare_digest(
        control_lease_record_sha256(record),
        acceptance.control_lease_record_sha256_audit,
    )
    if not scalars_match or not token_matches or not record_matches:
        raise GenerationStateControlBindingError("ControlLeaseRecord 与 acceptance 审计绑定不一致")
    return record


def require_current_active_lineage(
    current: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> tuple[ControlLeaseRecord, ...]:
    """将统一的 current-lineage 规则映射为状态领域错误。"""
    if type(control_lease_lineage) is not tuple:
        raise GenerationStateControlBindingError("必须显式提供 control lease lineage tuple")
    try:
        return verify_current_active_lineage(current, control_lease_lineage)
    except (ControlLeaseLineageError, FencingContractError) as exc:
        raise GenerationStateControlBindingError(str(exc)) from None


def require_unbound_control_lease(
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    *,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    event_at: str,
) -> tuple[ControlLeaseRecord, ...]:
    """复验尚未绑定 attempt 的状态边所使用的当前 control capability。"""
    try:
        verify_control_lease(lease_record, lease)
        lineage = require_current_active_lineage(lease_record, control_lease_lineage)
        verify_anchor_event_window(
            ControlLeaseAnchor.from_record(lease_record),
            event_at,
            lineage,
            lease_record,
            event_field="state updated_at",
        )
        return lineage
    except (
        ControlLeaseLineageError,
        FencingContractError,
        GenerationStateControlBindingError,
    ) as exc:
        raise GenerationStateControlBindingError(str(exc)) from None


def require_bound_control_lease(
    *,
    control_attempt_id: str | None,
    control_reservation_sha256: str | None,
    control_lease_record_sha256_audit: str | None,
    control_lease_epoch: int | None,
    lease_record: ControlLeaseRecord,
    lease: ControlLeaseProof,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
    state_updated_at: str,
) -> tuple[ControlLeaseRecord, ...]:
    """验证已绑定状态可由当前 lease 或其合法 recovery 后继继续推进。"""
    if (
        control_attempt_id is None
        or control_reservation_sha256 is None
        or control_lease_record_sha256_audit is None
        or control_lease_epoch is None
    ):
        raise GenerationStateControlBindingError("当前状态未绑定完整 control lease")
    try:
        verify_control_lease(lease_record, lease)
        lineage = require_current_active_lineage(lease_record, control_lease_lineage)
        if lease_record.attempt_id != control_attempt_id:
            raise GenerationStateControlBindingError("ControlLeaseRecord attempt 身份不一致")
        if lease_record.reservation_sha256 != control_reservation_sha256:
            raise GenerationStateControlBindingError("ControlLeaseRecord reservation 已漂移")
        if lease_record.epoch < control_lease_epoch:
            raise GenerationStateControlBindingError("ControlLeaseRecord epoch 已过期")
        verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=control_lease_record_sha256_audit,
                attempt_id=control_attempt_id,
                reservation_sha256=control_reservation_sha256,
                epoch=control_lease_epoch,
            ),
            state_updated_at,
            lineage,
            lease_record,
            event_field="state updated_at",
        )
        return lineage
    except (
        ControlLeaseLineageError,
        FencingContractError,
        GenerationStateControlBindingError,
    ) as exc:
        raise GenerationStateControlBindingError(str(exc)) from None


def require_acceptance_control_lease_lineage(
    acceptance: GenerationAcceptance,
    *,
    reservation_sha256: str,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> ControlLeaseRecord:
    """证明 acceptance 的完整 lease 审计锚点属于当前 recovery lineage。"""
    if type(acceptance) is not GenerationAcceptance:
        raise GenerationStateControlBindingError("只接受 GenerationAcceptance")
    try:
        return verify_anchor_event_window(
            ControlLeaseAnchor(
                record_sha256=acceptance.control_lease_record_sha256_audit,
                attempt_id=acceptance.attempt_id,
                reservation_sha256=reservation_sha256,
                epoch=acceptance.control_lease_epoch_audit,
                token_sha256=acceptance.control_token_sha256_audit,
            ),
            acceptance.accepted_at,
            control_lease_lineage,
            current_lease,
            event_field="acceptance accepted_at",
        )
    except ControlLeaseLineageError as exc:
        raise GenerationStateControlBindingError(str(exc)) from None


__all__ = [
    "GenerationStateControlBindingError",
    "require_acceptance_control_lease_lineage",
    "require_bound_control_lease",
    "require_current_active_lineage",
    "require_unbound_control_lease",
    "verify_active_control_lease_acceptance",
]
