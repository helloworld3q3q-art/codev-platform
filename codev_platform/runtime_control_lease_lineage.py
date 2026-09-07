"""控制租约 recovery issuance lineage 的纯领域校验。"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_attempt_id,
    require_sha256,
    require_strictly_later,
)
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    ControlLeaseStatus,
    control_lease_record_sha256,
)


_MAX_ACTIVE_LINEAGE_RECORDS = 128


class ControlLeaseLineageError(ValueError):
    """控制租约 lineage 无法证明连续、精确的 recovery 来源。"""


@dataclass(frozen=True, slots=True)
class ControlLeaseAnchor:
    """上层审计对象保存的单条活动 control lease 精确锚点。"""

    record_sha256: str
    attempt_id: str
    epoch: int
    reservation_sha256: str | None = None
    token_sha256: str | None = None

    def __post_init__(self) -> None:
        try:
            require_sha256(self.record_sha256, field="record_sha256")
            require_attempt_id(self.attempt_id)
            if self.reservation_sha256 is not None:
                require_sha256(self.reservation_sha256, field="reservation_sha256")
            if self.token_sha256 is not None:
                require_sha256(self.token_sha256, field="token_sha256")
        except RuntimeContractSupportError as exc:
            raise ControlLeaseLineageError(str(exc)) from None
        if type(self.epoch) is not int or self.epoch < 1:
            raise ControlLeaseLineageError("epoch 必须是有效正整数")

    @classmethod
    def from_record(cls, value: ControlLeaseRecord) -> ControlLeaseAnchor:
        """从一条已校验活动 lease 生成不可歧义的审计锚点。"""
        _require_active_record(value)
        return cls(
            record_sha256=control_lease_record_sha256(value),
            attempt_id=value.attempt_id,
            reservation_sha256=value.reservation_sha256,
            epoch=value.epoch,
            token_sha256=value.token_sha256,
        )


def verify_recovery_edge(
    previous_active: ControlLeaseRecord,
    next_active: ControlLeaseRecord,
) -> ControlLeaseRecord:
    """验证一条 recovery issuance 边，不把 retirement 混入该链。"""
    _require_active_record(previous_active)
    _require_active_record(next_active)
    if (
        previous_active.attempt_id != next_active.attempt_id
        or previous_active.reservation_sha256 != next_active.reservation_sha256
    ):
        raise ControlLeaseLineageError("recovery lineage 的 attempt 或 reservation 不一致")
    if next_active.epoch <= previous_active.epoch:
        raise ControlLeaseLineageError("recovery lineage epoch 必须严格提升")
    if hmac.compare_digest(previous_active.token_sha256, next_active.token_sha256):
        raise ControlLeaseLineageError("recovery lineage 必须轮换 token")
    expected_predecessor = control_lease_record_sha256(previous_active)
    if not hmac.compare_digest(next_active.predecessor_sha256 or "", expected_predecessor):
        raise ControlLeaseLineageError("recovery lineage predecessor 摘要不匹配")
    try:
        require_strictly_later(
            next_active.issued_at,
            previous_active.issued_at,
            field="issued_at",
            boundary_field="前驱 control lease issued_at",
        )
    except RuntimeContractSupportError as exc:
        raise ControlLeaseLineageError(str(exc)) from None
    return next_active


def verify_active_lineage(
    records: tuple[ControlLeaseRecord, ...],
) -> tuple[ControlLeaseRecord, ...]:
    """验证以初始活动 lease 起始的连续、有限、不可分叉 recovery 链。"""
    if type(records) is not tuple or not records:
        raise ControlLeaseLineageError("active lineage 必须是非空 tuple")
    if len(records) > _MAX_ACTIVE_LINEAGE_RECORDS:
        raise ControlLeaseLineageError("active lineage 超出固定上限")
    for record in records:
        _require_active_record(record)
    if records[0].predecessor_sha256 is not None:
        raise ControlLeaseLineageError("active lineage 首节点不得含前驱摘要")
    digests = tuple(control_lease_record_sha256(record) for record in records)
    if len(set(digests)) != len(digests):
        raise ControlLeaseLineageError("active lineage 不得含重复记录")
    for previous, current in zip(records, records[1:], strict=False):
        verify_recovery_edge(previous, current)
    return records


def verify_current_active_lineage(
    current_active: ControlLeaseRecord,
    supplied: tuple[ControlLeaseRecord, ...] | None,
) -> tuple[ControlLeaseRecord, ...]:
    """验证显式活动链，并确认末端精确等于当前 capability 记录。"""
    records = verify_active_lineage((current_active,) if supplied is None else supplied)
    _require_active_record(current_active)
    if not hmac.compare_digest(
        control_lease_record_sha256(records[-1]),
        control_lease_record_sha256(current_active),
    ):
        raise ControlLeaseLineageError("active lineage 末端不是当前 control lease")
    return records


def verify_anchor_ancestor(
    anchor: ControlLeaseAnchor,
    lineage: tuple[ControlLeaseRecord, ...],
    current_active: ControlLeaseRecord,
) -> ControlLeaseRecord:
    """证明精确审计锚点位于 current 活动 lease 的 lineage 上。"""
    if type(anchor) is not ControlLeaseAnchor:
        raise ControlLeaseLineageError("只接受 ControlLeaseAnchor")
    records = verify_current_active_lineage(current_active, lineage)
    for record in records:
        record_anchor = ControlLeaseAnchor.from_record(record)
        if (
            anchor.record_sha256 == record_anchor.record_sha256
            and anchor.attempt_id == record_anchor.attempt_id
            and anchor.epoch == record_anchor.epoch
            and (
                anchor.reservation_sha256 is None
                or anchor.reservation_sha256 == record_anchor.reservation_sha256
            )
            and (anchor.token_sha256 is None or anchor.token_sha256 == record_anchor.token_sha256)
        ):
            return record
    raise ControlLeaseLineageError("审计锚点不在当前 control lease lineage 上")


def verify_anchor_event_window(
    anchor: ControlLeaseAnchor,
    event_at: str,
    lineage: tuple[ControlLeaseRecord, ...],
    current_active: ControlLeaseRecord,
    *,
    event_field: str,
) -> ControlLeaseRecord:
    """证明审计事件严格发生在锚点签发与后继接管之间。"""
    records = verify_current_active_lineage(current_active, lineage)
    anchored = verify_anchor_ancestor(anchor, records, current_active)
    anchored_sha256 = control_lease_record_sha256(anchored)
    anchor_index = next(
        index
        for index, record in enumerate(records)
        if hmac.compare_digest(control_lease_record_sha256(record), anchored_sha256)
    )
    try:
        require_strictly_later(
            event_at,
            anchored.issued_at,
            field=event_field,
            boundary_field="审计锚点 control lease issued_at",
        )
        for successor in records[anchor_index + 1 :]:
            require_strictly_later(
                successor.issued_at,
                event_at,
                field="后继 control lease issued_at",
                boundary_field=event_field,
            )
    except RuntimeContractSupportError as exc:
        raise ControlLeaseLineageError(str(exc)) from None
    return anchored


def verify_retirement_transition(
    active: ControlLeaseRecord,
    retired: ControlLeaseRecord,
) -> ControlLeaseRecord:
    """验证 ACTIVE 到 tombstone 的精确状态转换。"""
    _require_active_record(active)
    if type(retired) is not ControlLeaseRecord:
        raise ControlLeaseLineageError("只接受 ControlLeaseRecord")
    if retired.status is not ControlLeaseStatus.RETIRED:
        raise ControlLeaseLineageError("retirement 结果必须是 RETIRED")
    if not hmac.compare_digest(
        retired.retired_from_sha256 or "",
        control_lease_record_sha256(active),
    ):
        raise ControlLeaseLineageError("tombstone 未精确绑定被退休活动 lease")
    return retired


def _require_active_record(value: object) -> None:
    if type(value) is not ControlLeaseRecord:
        raise ControlLeaseLineageError("只接受 ControlLeaseRecord")
    if value.status is not ControlLeaseStatus.ACTIVE:
        raise ControlLeaseLineageError("recovery lineage 只接受 ACTIVE control lease")


__all__ = [
    "ControlLeaseAnchor",
    "ControlLeaseLineageError",
    "verify_active_lineage",
    "verify_anchor_ancestor",
    "verify_anchor_event_window",
    "verify_current_active_lineage",
    "verify_recovery_edge",
    "verify_retirement_transition",
]
