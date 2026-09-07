"""TransactionJournal 直接构造与 decoder 的 1 MiB 闭包边界测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from codev_platform.core.runtime_models import canonical_json_bytes
from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_fencing import issue_control_lease
from codev_platform.runtime_transaction_codec import (
    decode_transaction_journal,
    encode_transaction_journal,
)
from codev_platform.runtime_transaction_contract import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    SERVE_PERMIT_RESOURCE_KIND,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    advance_transaction_action,
    create_transaction_journal,
    prepare_transaction_action,
)


MAX_JOURNAL_BYTES = 1_048_576
TOKEN = bytes(range(32))
_ACTION_TIME_ORIGIN = datetime(2026, 7, 19, 10, 1, tzinfo=UTC)


def _action_time(sequence: int) -> str:
    value = _ACTION_TIME_ORIGIN + timedelta(microseconds=sequence)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="e" * 64,
        controller_sha256="f" * 64,
        created_at="2026-07-19T10:00:00Z",
    )


def _attempt() -> DeploymentAttempt:
    reservation = _reservation()
    return verify_frozen_attempt(
        reservation,
        freeze_deployment_attempt(
            reservation,
            target_generation_id="b" * 64,
            baseline_generation_id="c" * 64,
            baseline_observation_sha256="d" * 64,
        ),
    )


def _action_history() -> tuple[TransactionAction, ...]:
    attempt = _attempt()
    record, proof = issue_control_lease(
        _reservation(),
        epoch=4,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )
    actions: list[TransactionAction] = []
    for step in range(1, 1_367):
        time_sequence = (step - 1) * 3
        first = step == 1
        prefix = f"resource-{step:04d}-"
        prepared = prepare_transaction_action(
            attempt,
            record,
            proof,
            step_sequence=step,
            intent=(
                TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT
                if first
                else TransactionActionIntent.APPLY_RESOURCE
            ),
            resource_kind=SERVE_PERMIT_RESOURCE_KIND if first else "configuration",
            resource_id=(
                CURRENT_SERVE_PERMIT_RESOURCE_ID if first else prefix + "x" * (255 - len(prefix))
            ),
            operation_sha256="1" * 64,
            before_sha256="2" * 64,
            after_sha256="3" * 64,
            recorded_at=_action_time(time_sequence + 1),
        )
        applied = advance_transaction_action(
            prepared,
            TransactionActionState.APPLIED,
            record,
            proof,
            recorded_at=_action_time(time_sequence + 2),
        )
        committed = advance_transaction_action(
            applied,
            TransactionActionState.COMMITTED,
            record,
            proof,
            recorded_at=_action_time(time_sequence + 3),
        )
        actions.extend((prepared, applied, committed))
    return tuple(actions[:4_096])


def _journal_values(
    empty: TransactionJournal,
    actions: tuple[TransactionAction, ...],
) -> dict[str, object]:
    return {
        "schema_version": empty.schema_version,
        "attempt_id": empty.attempt_id,
        "reservation_sha256": empty.reservation_sha256,
        "journal_genesis_sha256": empty.journal_genesis_sha256,
        "status": empty.status,
        "actions": actions,
        "terminal_evidence_sha256": empty.terminal_evidence_sha256,
        "created_at": empty.created_at,
        "updated_at": actions[-1].recorded_at if actions else empty.created_at,
        "completed_at": empty.completed_at,
    }


def test_journal直接构造在1MiB边界内往返且下一合法记录被拒绝() -> None:
    empty = create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )
    actions = _action_history()
    low, high = 0, len(actions)
    while low < high:
        middle = (low + high + 1) // 2
        size = len(canonical_json_bytes(_journal_values(empty, actions[:middle])))
        if size <= MAX_JOURNAL_BYTES:
            low = middle
        else:
            high = middle - 1

    accepted_values = _journal_values(empty, actions[:low])
    oversized_values = _journal_values(empty, actions[: low + 1])
    assert len(canonical_json_bytes(accepted_values)) <= MAX_JOURNAL_BYTES
    assert len(canonical_json_bytes(oversized_values)) > MAX_JOURNAL_BYTES

    accepted = TransactionJournal(**accepted_values)
    encoded = encode_transaction_journal(accepted)
    assert len(encoded) <= MAX_JOURNAL_BYTES
    assert decode_transaction_journal(encoded) == accepted
    with pytest.raises(TransactionContractError, match="1 MiB"):
        TransactionJournal(**oversized_values)
