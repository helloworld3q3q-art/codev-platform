"""AttemptReservation 早期控制链顺序契约测试。"""

from __future__ import annotations

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
)
from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_recovery_contract import (
    RecoveryContractError,
    create_recovery_envelope,
    verify_recovery_envelope,
)
from codev_platform.runtime_fencing import (
    FencingContractError,
    issue_control_lease,
    recover_control_lease,
)
from codev_platform.runtime_transaction_contract import (
    TransactionContractError,
    create_transaction_journal,
)


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


def _attempt() -> DeploymentAttempt:
    return freeze_deployment_attempt(
        _reservation(),
        target_generation_id="b" * 64,
        baseline_generation_id="c" * 64,
        baseline_observation_sha256="d" * 64,
    )


def test_journal_genesis只接受早期reservation() -> None:
    reservation = _reservation()

    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )

    assert journal.attempt_id == reservation.attempt_id
    with pytest.raises(TransactionContractError, match="AttemptReservation"):
        create_transaction_journal(
            _attempt(),  # type: ignore[arg-type]
            created_at="2026-07-19T10:00:30Z",
        )


def test_journal_genesis精确绑定reservation完整身份() -> None:
    reservation = _reservation()
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )

    assert journal.reservation_sha256 == canonical_sha256(reservation)
    for changed in (
        _reservation(operation=AttemptOperation.ROLLBACK),
        _reservation(plan_sha256="1" * 64),
        _reservation(controller_sha256="2" * 64),
    ):
        other = create_transaction_journal(
            changed,
            created_at="2026-07-19T10:00:30Z",
        )
        assert other.reservation_sha256 != journal.reservation_sha256
        assert other.journal_genesis_sha256 != journal.journal_genesis_sha256


def test_recovery_envelope只接受早期reservation() -> None:
    reservation = _reservation()
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )

    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controllers/controller-v1",
        interpreter_relative="controllers/controller-v1/venv/bin/python",
        interpreter_sha256="2" * 64,
        transaction_store_id="runtime-transaction-store-v1",
        created_at="2026-07-19T10:01:00Z",
    )

    assert envelope.attempt_id == reservation.attempt_id
    with pytest.raises(RecoveryContractError, match="AttemptReservation"):
        create_recovery_envelope(
            _attempt(),  # type: ignore[arg-type]
            journal,
            controller_root_relative=envelope.controller_root_relative,
            interpreter_relative=envelope.interpreter_relative,
            interpreter_sha256=envelope.interpreter_sha256,
            transaction_store_id=envelope.transaction_store_id,
            created_at=envelope.created_at,
        )


def test_recovery_envelope显式绑定reservation摘要() -> None:
    reservation = _reservation()
    journal = create_transaction_journal(
        reservation,
        created_at="2026-07-19T10:00:30Z",
    )
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controllers/controller-v1",
        interpreter_relative="controllers/controller-v1/venv/bin/python",
        interpreter_sha256="2" * 64,
        transaction_store_id="runtime-transaction-store-v1",
        created_at="2026-07-19T10:01:00Z",
    )

    assert envelope.reservation_sha256 == journal.reservation_sha256
    with pytest.raises(RecoveryContractError, match="reservation"):
        verify_recovery_envelope(
            envelope,
            _reservation(plan_sha256="1" * 64),
            journal,
        )


def test_initial_control_lease只从reservation签发() -> None:
    reservation = _reservation()

    record, proof = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:30Z",
    )

    assert record.attempt_id == reservation.attempt_id == proof.attempt_id
    with pytest.raises(FencingContractError, match="AttemptReservation"):
        issue_control_lease(
            _attempt(),  # type: ignore[arg-type]
            epoch=1,
            token=bytes(range(32)),
            owner="controller-a",
            issued_at="2026-07-19T10:01:30Z",
        )


def test_control_lease持久记录绑定schema与reservation摘要() -> None:
    reservation = _reservation()
    record, _ = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:30Z",
    )

    assert record.schema_version == 1
    assert record.reservation_sha256 == canonical_sha256(reservation)


def test_control_lease_takeover只接受同一reservation() -> None:
    reservation = _reservation()
    current, _ = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-19T10:01:30Z",
    )

    recovered, proof = recover_control_lease(
        current,
        reservation,
        epoch=2,
        token=bytes(range(32, 64)),
        owner="controller-b",
        issued_at="2026-07-19T10:02:00Z",
    )

    assert recovered.reservation_sha256 == current.reservation_sha256
    assert proof.epoch == 2
    with pytest.raises(FencingContractError, match="reservation"):
        recover_control_lease(
            current,
            _reservation(plan_sha256="1" * 64),
            epoch=2,
            token=bytes(range(32, 64)),
            owner="controller-b",
            issued_at="2026-07-19T10:02:00Z",
        )
    with pytest.raises(FencingContractError, match="AttemptReservation"):
        recover_control_lease(
            current,
            _attempt(),  # type: ignore[arg-type]
            epoch=2,
            token=bytes(range(32, 64)),
            owner="controller-b",
            issued_at="2026-07-19T10:02:00Z",
        )
