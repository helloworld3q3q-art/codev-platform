"""控制租约接管后的事务 journal 幂等语义测试。"""

from __future__ import annotations

import dataclasses

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    DeploymentAttempt,
    freeze_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    ControlLeaseRecord,
    ControlLeaseStatus,
    control_lease_record_sha256,
    issue_control_lease,
    recover_control_lease,
)
from codev_platform.runtime_transaction_contract import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionContractError,
    TransactionJournal,
    advance_transaction_action,
    append_transaction_action,
    create_transaction_journal,
    prepare_transaction_action,
)

TOKEN = bytes(range(32))
TAKEOVER_TOKEN = bytes(range(32, 64))
WRONG_TOKEN = bytes(range(64, 96))


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


def _control() -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    return issue_control_lease(
        _reservation(),
        epoch=4,
        token=TOKEN,
        owner="controller-a",
        issued_at="2026-07-19T10:00:30Z",
    )


def _takeover(
    record: ControlLeaseRecord,
    *,
    issued_at: str = "2026-07-19T10:01:30Z",
) -> tuple[ControlLeaseRecord, ControlLeaseProof]:
    return recover_control_lease(
        record,
        _reservation(),
        epoch=5,
        token=TAKEOVER_TOKEN,
        owner="recovery-a",
        issued_at=issued_at,
    )


def _journal() -> TransactionJournal:
    return create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )


def _prepared(
    control: tuple[ControlLeaseRecord, ControlLeaseProof],
) -> TransactionAction:
    return prepare_transaction_action(
        _attempt(),
        *control,
        step_sequence=1,
        intent=TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT,
        resource_kind="serve-permit",
        resource_id=CURRENT_SERVE_PERMIT_RESOURCE_ID,
        operation_sha256="1" * 64,
        before_sha256="2" * 64,
        after_sha256="3" * 64,
        recorded_at="2026-07-19T10:01:00Z",
    )


def _retired_control(
    record: ControlLeaseRecord,
    proof: ControlLeaseProof,
) -> ControlLeaseRecord:
    del proof
    return dataclasses.replace(
        record,
        status=ControlLeaseStatus.RETIRED,
        terminal_journal_sha256="7" * 64,
        terminal_evidence_sha256="8" * 64,
        retired_at="2026-07-19T10:10:00Z",
        retired_from_sha256=control_lease_record_sha256(record),
    )


def _persisted_action(
    state: TransactionActionState,
) -> tuple[
    TransactionJournal,
    TransactionAction,
    ControlLeaseRecord,
    ControlLeaseProof,
]:
    record, proof = _control()
    action = _prepared((record, proof))
    journal = append_transaction_action(_journal(), action, record, proof)
    if state is not TransactionActionState.PREPARED:
        action = advance_transaction_action(
            action,
            TransactionActionState.APPLIED,
            record,
            proof,
            recorded_at="2026-07-19T10:02:00Z",
        )
        journal = append_transaction_action(journal, action, record, proof)
    if state is TransactionActionState.COMMITTED:
        action = advance_transaction_action(
            action,
            TransactionActionState.COMMITTED,
            record,
            proof,
            recorded_at="2026-07-19T10:03:00Z",
        )
        journal = append_transaction_action(journal, action, record, proof)
    return journal, action, record, proof


@pytest.mark.parametrize(
    "state",
    (TransactionActionState.PREPARED, TransactionActionState.APPLIED),
)
def test_takeover后精确重试已落盘旧动作保持同一journal(
    state: TransactionActionState,
) -> None:
    journal, action, record, _ = _persisted_action(state)
    original_history = journal.actions

    retried = append_transaction_action(journal, action, *_takeover(record))

    assert retried is journal
    assert retried.actions is original_history


@pytest.mark.parametrize(
    ("journal_state", "history_index"),
    (
        (TransactionActionState.APPLIED, 0),
        (TransactionActionState.COMMITTED, 0),
        (TransactionActionState.COMMITTED, 1),
    ),
)
def test_takeover后精确重试完整历史中的旧状态保持幂等(
    journal_state: TransactionActionState,
    history_index: int,
) -> None:
    journal, _, record, _ = _persisted_action(journal_state)
    original_history = journal.actions
    historical_action = original_history[history_index]

    retried = append_transaction_action(journal, historical_action, *_takeover(record))

    assert retried is journal
    assert retried.actions is original_history


@pytest.mark.parametrize("invalid_lease", ("wrong-token", "retired"))
def test_历史旧状态在幂等返回前仍校验当前lease(invalid_lease: str) -> None:
    journal, _, record, _ = _persisted_action(TransactionActionState.COMMITTED)
    historical_action = journal.actions[0]
    recovered_record, recovered_proof = _takeover(record)
    if invalid_lease == "wrong-token":
        current_record = recovered_record
        current_proof = ControlLeaseProof(
            recovered_proof.attempt_id,
            recovered_proof.epoch,
            WRONG_TOKEN,
        )
    else:
        current_record = _retired_control(recovered_record, recovered_proof)
        current_proof = recovered_proof

    with pytest.raises(TransactionContractError):
        append_transaction_action(
            journal,
            historical_action,
            current_record,
            current_proof,
        )


def test_applied历史幂等后可用接管lease继续提交() -> None:
    journal, applied, record, _ = _persisted_action(TransactionActionState.APPLIED)
    original_history = journal.actions
    recovered_record, recovered_proof = _takeover(
        record,
        issued_at="2026-07-19T10:02:30Z",
    )

    retried = append_transaction_action(
        journal,
        applied,
        recovered_record,
        recovered_proof,
    )
    committed = advance_transaction_action(
        applied,
        TransactionActionState.COMMITTED,
        recovered_record,
        recovered_proof,
        recorded_at="2026-07-19T10:03:00Z",
        control_lease_lineage=(record, recovered_record),
    )
    completed = append_transaction_action(
        retried,
        committed,
        recovered_record,
        recovered_proof,
        control_lease_lineage=(record, recovered_record),
    )

    assert retried is journal
    assert completed.actions == (*original_history, committed)
    assert len(completed.actions) == 3
    assert all(action.control_lease_epoch_audit == record.epoch for action in original_history)
    assert completed.actions[-1].state is TransactionActionState.COMMITTED
    assert completed.actions[-1].control_lease_epoch_audit == recovered_record.epoch
    assert completed.actions[-1].control_token_sha256_audit == recovered_record.token_sha256


def test_历史旧状态identity漂移仍拒绝() -> None:
    journal, _, record, _ = _persisted_action(TransactionActionState.COMMITTED)
    drifted = dataclasses.replace(journal.actions[0], operation_sha256="7" * 64)

    with pytest.raises(TransactionContractError, match="身份"):
        append_transaction_action(journal, drifted, *_takeover(record))


def test_完整历史不存在的旧审计状态仍拒绝() -> None:
    journal, applied, record, proof = _persisted_action(TransactionActionState.APPLIED)
    old_committed = advance_transaction_action(
        applied,
        TransactionActionState.COMMITTED,
        record,
        proof,
        recorded_at="2026-07-19T10:03:00Z",
    )

    with pytest.raises(TransactionContractError, match="epoch"):
        append_transaction_action(journal, old_committed, *_takeover(record))


@pytest.mark.parametrize("invalid_lease", ("wrong-token", "retired"))
def test_已存在动作在幂等返回前仍校验当前lease(invalid_lease: str) -> None:
    journal, action, record, proof = _persisted_action(TransactionActionState.PREPARED)
    if invalid_lease == "wrong-token":
        current_record = record
        current_proof = ControlLeaseProof(proof.attempt_id, proof.epoch, WRONG_TOKEN)
    else:
        current_record = _retired_control(record, proof)
        current_proof = proof

    with pytest.raises(TransactionContractError):
        append_transaction_action(journal, action, current_record, current_proof)


def test_takeover后真实下一状态使用新lease审计追加() -> None:
    journal, prepared, record, _ = _persisted_action(TransactionActionState.PREPARED)
    recovered_record, recovered_proof = _takeover(record)
    applied = advance_transaction_action(
        prepared,
        TransactionActionState.APPLIED,
        recovered_record,
        recovered_proof,
        recorded_at="2026-07-19T10:02:00Z",
        control_lease_lineage=(record, recovered_record),
    )

    advanced = append_transaction_action(
        journal,
        applied,
        recovered_record,
        recovered_proof,
        control_lease_lineage=(record, recovered_record),
    )

    assert advanced.actions == (prepared, applied)
    assert applied.control_lease_epoch_audit == recovered_record.epoch
    assert applied.control_token_sha256_audit == recovered_record.token_sha256


def test_空journal不能用旧审计动作搭配接管lease追加() -> None:
    record, proof = _control()
    old_action = _prepared((record, proof))

    with pytest.raises(TransactionContractError, match="epoch"):
        append_transaction_action(_journal(), old_action, *_takeover(record))


def test_不存在动作不能用旧审计搭配接管lease追加() -> None:
    record, proof = _control()
    first = _prepared((record, proof))
    journal = append_transaction_action(_journal(), first, record, proof)
    for state, recorded_at in (
        (TransactionActionState.APPLIED, "2026-07-19T10:02:00Z"),
        (TransactionActionState.COMMITTED, "2026-07-19T10:03:00Z"),
    ):
        first = advance_transaction_action(
            first,
            state,
            record,
            proof,
            recorded_at=recorded_at,
        )
        journal = append_transaction_action(journal, first, record, proof)
    old_next = prepare_transaction_action(
        _attempt(),
        record,
        proof,
        step_sequence=2,
        intent=TransactionActionIntent.APPLY_RESOURCE,
        resource_kind="configuration",
        resource_id="platform/config.json",
        operation_sha256="4" * 64,
        before_sha256="5" * 64,
        after_sha256="6" * 64,
        recorded_at="2026-07-19T10:04:00Z",
    )

    with pytest.raises(TransactionContractError, match="epoch"):
        append_transaction_action(journal, old_next, *_takeover(record))
