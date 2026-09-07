"""控制租约 recovery issuance lineage 的纯领域契约测试。"""

from __future__ import annotations

import dataclasses
import importlib

import pytest

from codev_platform.runtime_attempt_contract import AttemptOperation, AttemptReservation
from codev_platform.runtime_fencing import issue_control_lease, recover_control_lease


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-20T10:00:00Z",
    )


def _lineage_module():
    return importlib.import_module("codev_platform.runtime_control_lease_lineage")


def _two_active_leases():
    initial, _ = issue_control_lease(
        _reservation(),
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-20T10:01:00Z",
    )
    recovered, _ = recover_control_lease(
        initial,
        _reservation(),
        epoch=3,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-20T10:02:00Z",
    )
    return initial, recovered


def test_active_lineage接受严格递增的完整前驱链() -> None:
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()

    assert lineage.verify_active_lineage((initial, recovered)) == (
        initial,
        recovered,
    )


@pytest.mark.parametrize(
    "records",
    (
        lambda initial, recovered: (recovered,),
        lambda initial, recovered: (recovered, initial),
        lambda initial, recovered: (
            dataclasses.replace(initial, owner="controller-b"),
            recovered,
        ),
    ),
)
def test_active_lineage拒绝缺失倒序或篡改完整前驱(records) -> None:
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()

    with pytest.raises(lineage.ControlLeaseLineageError):
        lineage.verify_active_lineage(records(initial, recovered))


@pytest.mark.parametrize(
    "mutate",
    (
        lambda initial, recovered: dataclasses.replace(
            recovered,
            attempt_id="d" * 32,
        ),
        lambda initial, recovered: dataclasses.replace(
            recovered,
            reservation_sha256="d" * 64,
        ),
        lambda initial, recovered: dataclasses.replace(
            recovered,
            token_sha256=initial.token_sha256,
        ),
        lambda initial, recovered: dataclasses.replace(
            recovered,
            issued_at=initial.issued_at,
        ),
        lambda initial, recovered: dataclasses.replace(
            recovered,
            predecessor_sha256="d" * 64,
        ),
    ),
)
def test_active_lineage拒绝recovery身份能力时间或前驱漂移(mutate) -> None:
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()

    with pytest.raises(lineage.ControlLeaseLineageError):
        lineage.verify_active_lineage((initial, mutate(initial, recovered)))


def test_anchor必须是当前活动链上的精确完整记录() -> None:
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()
    records = lineage.verify_active_lineage((initial, recovered))
    anchor = lineage.ControlLeaseAnchor.from_record(initial)

    assert lineage.verify_anchor_ancestor(anchor, records, recovered) is initial
    forged = dataclasses.replace(initial, owner="controller-b")
    forged_anchor = lineage.ControlLeaseAnchor.from_record(forged)
    with pytest.raises(lineage.ControlLeaseLineageError):
        lineage.verify_anchor_ancestor(forged_anchor, records, recovered)


def test审计事件必须落在锚点生效与后继接管之间() -> None:
    """旧 lease 的证据不能伪造为发生在新 lease 接管之后。"""
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()
    anchor = lineage.ControlLeaseAnchor.from_record(initial)

    assert (
        lineage.verify_anchor_event_window(
            anchor,
            "2026-07-20T10:01:30Z",
            (initial, recovered),
            recovered,
            event_field="action recorded_at",
        )
        is initial
    )
    with pytest.raises(lineage.ControlLeaseLineageError, match="后继"):
        lineage.verify_anchor_event_window(
            anchor,
            "2026-07-20T10:02:30Z",
            (initial, recovered),
            recovered,
            event_field="action recorded_at",
        )
    with pytest.raises(lineage.ControlLeaseLineageError, match="锚点"):
        lineage.verify_anchor_event_window(
            anchor,
            "2026-07-20T10:00:59Z",
            (initial, recovered),
            recovered,
            event_field="action recorded_at",
        )


def test当前活动租约必须精确位于显式lineage末端() -> None:
    initial, recovered = _two_active_leases()
    lineage = _lineage_module()

    assert lineage.verify_current_active_lineage(recovered, (initial, recovered)) == (
        initial,
        recovered,
    )
    with pytest.raises(lineage.ControlLeaseLineageError, match="末端"):
        lineage.verify_current_active_lineage(initial, (initial, recovered))
