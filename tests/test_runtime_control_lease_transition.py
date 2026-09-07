"""control lease pending transition 的纯契约与规范编码测试。"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from codev_platform.core.runtime_models import canonical_json_bytes
from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_control_lease_transition import (
    ControlLeaseTransitionError,
    ControlLeaseTransitionIntent,
    ControlLeaseTransitionKind,
    decode_control_lease_transition_intent,
    encode_control_lease_transition_intent,
)
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    ControlLeaseStatus,
    control_lease_record_sha256,
    issue_control_lease,
    recover_control_lease,
)


def _reservation(*, attempt_id: str = "a" * 32) -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id=attempt_id,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _active_pair() -> tuple[ControlLeaseRecord, ControlLeaseRecord]:
    reservation = _reservation()
    current, _ = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-21T00:00:01Z",
    )
    successor, _ = recover_control_lease(
        current,
        reservation,
        epoch=2,
        token=bytes(range(32, 64)),
        owner="controller-b",
        issued_at="2026-07-21T00:00:02Z",
    )
    return current, successor


def _retired(record: ControlLeaseRecord) -> ControlLeaseRecord:
    return ControlLeaseRecord(
        schema_version=record.schema_version,
        attempt_id=record.attempt_id,
        reservation_sha256=record.reservation_sha256,
        epoch=record.epoch,
        token_sha256=record.token_sha256,
        owner=record.owner,
        status=ControlLeaseStatus.RETIRED,
        issued_at=record.issued_at,
        predecessor_sha256=record.predecessor_sha256,
        terminal_journal_sha256="d" * 64,
        terminal_evidence_sha256="e" * 64,
        retired_at="2026-07-21T00:00:03Z",
        retired_from_sha256=control_lease_record_sha256(record),
    )


def _takeover_intent() -> ControlLeaseTransitionIntent:
    current, successor = _active_pair()
    return ControlLeaseTransitionIntent(
        schema_version=1,
        kind=ControlLeaseTransitionKind.TAKEOVER,
        expected_record=current,
        next_record=successor,
    )


def test四字段规范往返且摘要仅由完整record派生() -> None:
    """意图 JSON 只含四字段，所有摘要均为内存派生属性。"""
    intent = _takeover_intent()

    payload = encode_control_lease_transition_intent(intent)
    decoded = decode_control_lease_transition_intent(payload)
    values = json.loads(payload)

    assert decoded == intent
    assert payload == encode_control_lease_transition_intent(decoded)
    assert set(values) == {"schema_version", "kind", "expected_record", "next_record"}
    assert "expected_record_sha256" not in values
    assert "next_record_sha256" not in values
    assert "token" not in values and "proof" not in values and "path" not in values
    assert intent.expected_record_sha256 == control_lease_record_sha256(
        intent.expected_record,
    )
    assert intent.next_record_sha256 == control_lease_record_sha256(intent.next_record)


@pytest.mark.parametrize("mutation", ("未知字段", "缺少字段", "重复字段"))
def test根字段集合必须严格且拒绝重复键(mutation: str) -> None:
    """未知、缺失和重复根字段不能形成可恢复提交决定。"""
    payload = encode_control_lease_transition_intent(_takeover_intent())
    values = json.loads(payload)
    if mutation == "未知字段":
        values["unexpected"] = True
        invalid = canonical_json_bytes(values)
    elif mutation == "缺少字段":
        del values["expected_record"]
        invalid = canonical_json_bytes(values)
    else:
        text = payload.decode("utf-8")
        invalid = text.replace("{", '{"schema_version":1,', 1).encode("utf-8")

    with pytest.raises(ControlLeaseTransitionError):
        decode_control_lease_transition_intent(invalid)


def test嵌套record字段漂移与非规范字节均被拒绝() -> None:
    """嵌套 record 既不能放宽字段集，也不能绕开整个 payload 的规范性。"""
    payload = encode_control_lease_transition_intent(_takeover_intent())
    values = json.loads(payload)
    values["next_record"]["unexpected"] = "drift"

    with pytest.raises(ControlLeaseTransitionError):
        decode_control_lease_transition_intent(canonical_json_bytes(values))

    reordered = json.dumps(
        json.loads(payload),
        ensure_ascii=True,
        separators=(",", ": "),
        sort_keys=False,
    ).encode("utf-8")
    with pytest.raises(ControlLeaseTransitionError, match="规范"):
        decode_control_lease_transition_intent(reordered)


@pytest.mark.parametrize(
    ("kind", "expected", "next_record"),
    (
        pytest.param(
            ControlLeaseTransitionKind.INITIAL,
            lambda current: current,
            lambda _current, successor: successor,
            id="initial-不能引用活动旧记录",
        ),
        pytest.param(
            ControlLeaseTransitionKind.TAKEOVER,
            lambda _current: None,
            lambda _current, successor: successor,
            id="takeover-必须引用活动旧记录",
        ),
        pytest.param(
            ControlLeaseTransitionKind.TAKEOVER,
            lambda current: current,
            lambda _current, successor: replace(successor, epoch=3),
            id="takeover-epoch必须只提升一级",
        ),
        pytest.param(
            ControlLeaseTransitionKind.RETIRE,
            lambda current: current,
            lambda _current, successor: _retired(successor),
            id="retire-稳定字段必须精确继承",
        ),
    ),
)
def test各种转换kind必须满足纯record形状(
    kind: ControlLeaseTransitionKind,
    expected,
    next_record,
) -> None:
    """构造器只接受不需要路径、锁或 capability 的合法状态边。"""
    current, successor = _active_pair()

    with pytest.raises(ControlLeaseTransitionError):
        ControlLeaseTransitionIntent(
            schema_version=1,
            kind=kind,
            expected_record=expected(current),
            next_record=next_record(current, successor),
        )


def test不同attempt的retired前驱允许新的initial而同attempt拒绝() -> None:
    """initial 的旧 current 可为另一个 attempt 的 tombstone，不形成双真值。"""
    old_current, _ = _active_pair()
    old_tombstone = _retired(old_current)
    new_reservation = _reservation(attempt_id="f" * 32)
    next_record, _ = issue_control_lease(
        new_reservation,
        epoch=1,
        token=bytes(range(64, 96)),
        owner="controller-new",
        issued_at="2026-07-21T00:00:04Z",
    )

    intent = ControlLeaseTransitionIntent(
        schema_version=1,
        kind=ControlLeaseTransitionKind.INITIAL,
        expected_record=old_tombstone,
        next_record=next_record,
    )
    assert (
        decode_control_lease_transition_intent(
            encode_control_lease_transition_intent(intent),
        )
        == intent
    )

    with pytest.raises(ControlLeaseTransitionError, match="不同 attempt"):
        ControlLeaseTransitionIntent(
            schema_version=1,
            kind=ControlLeaseTransitionKind.INITIAL,
            expected_record=old_tombstone,
            next_record=replace(next_record, attempt_id=old_tombstone.attempt_id),
        )
