from __future__ import annotations

import dataclasses
import importlib
import inspect

import pytest

from codev_platform.runtime_fencing import issue_control_lease, recover_control_lease
from codev_platform.runtime_generation_state import GenerationMode, generation_state_sha256
from codev_platform.runtime_serving_permit import serving_permit_sha256
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalError,
    TransactionTerminalOutcome,
    decode_transaction_terminal_evidence,
    encode_transaction_terminal_evidence,
    transaction_terminal_evidence_sha256,
)
from codev_platform import runtime_transaction_contract as transaction
from tests.runtime_transaction_terminal_support import (
    _public_terminal_input,
    _reservation,
    _safety_terminal_input,
)


def test_complete_transaction_journal显式进入completed终态() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()

    completed, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        completed_at="2026-07-19T10:09:00Z",
        control_lease_lineage=(record,),
    )

    assert completed.status is transaction.TransactionJournalStatus.COMPLETED
    assert completed.terminal_evidence_sha256 == (transaction_terminal_evidence_sha256(evidence))
    assert (
        transaction.transaction_recovery_directive(completed)
        is transaction.TransactionRecoveryDirective.TERMINAL
    )


def test_retire_control_lease只接受匹配的completed_journal与evidence() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    completed, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        completed_at="2026-07-19T10:09:00Z",
        control_lease_lineage=(record,),
    )

    retired = terminal.retire_control_lease(
        record,
        proof,
        completed_journal=completed,
        terminal_evidence=evidence,
        attempt=attempt,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        retired_at="2026-07-19T10:10:00Z",
        control_lease_lineage=(record,),
    )

    assert retired.terminal_journal_sha256 == transaction.transaction_journal_sha256(completed)
    assert retired.terminal_evidence_sha256 == (transaction_terminal_evidence_sha256(evidence))


def test_fencing不再暴露任意摘要退役入口() -> None:
    fencing = importlib.import_module("codev_platform.runtime_fencing")

    assert not hasattr(fencing, "retire_control_lease")


def test_terminal不再重导出终态证据模型或codec() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")

    for name in (
        "TransactionTerminalError",
        "TransactionTerminalEvidence",
        "TransactionTerminalOutcome",
        "decode_transaction_terminal_evidence",
        "encode_transaction_terminal_evidence",
        "transaction_terminal_evidence_sha256",
    ):
        assert not hasattr(terminal, name)


def test公开terminal入口必须显式注入control_lease_lineage() -> None:
    """终态完成、复验与退租都不得隐式采用 singleton lineage。"""
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")

    for entry in (
        terminal.complete_transaction_journal,
        terminal.verify_transaction_terminal_context,
        terminal.retire_control_lease,
    ):
        parameter = inspect.signature(entry).parameters["control_lease_lineage"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty


def test公开terminal入口拒绝显式None_control_lease_lineage() -> None:
    """显式传入 None 也必须 fail closed，不能回退为单节点谱系。"""
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    completed, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        completed_at="2026-07-19T10:09:00Z",
        control_lease_lineage=(record,),
    )

    with pytest.raises(TransactionTerminalError, match="必须显式提供"):
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=None,
        )
    with pytest.raises(TransactionTerminalError, match="必须显式提供"):
        terminal.verify_transaction_terminal_context(
            completed,
            evidence,
            attempt=attempt,
            current_lease=record,
            control_lease_lineage=None,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
        )
    with pytest.raises(TransactionTerminalError, match="必须显式提供"):
        terminal.retire_control_lease(
            record,
            proof,
            completed_journal=completed,
            terminal_evidence=evidence,
            attempt=attempt,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            retired_at="2026-07-19T10:10:00Z",
            control_lease_lineage=None,
        )


def test_terminal_evidence严格codec往返() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    _, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        completed_at="2026-07-19T10:09:00Z",
        control_lease_lineage=(record,),
    )

    assert (
        decode_transaction_terminal_evidence(encode_transaction_terminal_evidence(evidence))
        == evidence
    )


def test_complete_transaction_journal从typed_safety_context内部派生摘要() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, safety_state, acceptance, fence = _safety_terminal_input()

    completed, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=safety_state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        completed_at="2026-07-19T10:09:00Z",
        control_lease_lineage=(record,),
    )

    assert completed.status is transaction.TransactionJournalStatus.COMPLETED
    assert evidence.final_state_sha256 == generation_state_sha256(safety_state)
    assert evidence.acceptance_sha256 is not None
    assert evidence.serving_fence_sha256 is not None
    assert evidence.serve_permit_sha256 is None


def test_target_committed绑定完整typed公开稳态与permit() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence, permit = _public_terminal_input(
        target_committed=True
    )

    completed, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.TARGET_COMMITTED,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=permit,
        completed_at="2026-07-19T10:12:00Z",
        control_lease_lineage=(record,),
    )

    assert evidence.serve_permit_sha256 == serving_permit_sha256(permit)
    assert (
        terminal.verify_transaction_terminal_context(
            completed,
            evidence,
            attempt=attempt,
            current_lease=record,
            control_lease_lineage=(record,),
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=permit,
        )
        is completed
    )


def test_target_committed只接受可验证的acceptance_control_lineage() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, _, state, acceptance, fence, permit = _public_terminal_input(
        target_committed=True
    )
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=2,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-19T10:11:30Z",
    )

    with pytest.raises(TransactionTerminalError):
        terminal.complete_transaction_journal(
            journal,
            recovered_record,
            recovered_proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.TARGET_COMMITTED,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=permit,
            completed_at="2026-07-19T10:12:00Z",
            control_lease_lineage=(recovered_record,),
        )

    completed, evidence = terminal.complete_transaction_journal(
        journal,
        recovered_record,
        recovered_proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.TARGET_COMMITTED,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=permit,
        control_lease_lineage=(record, recovered_record),
        completed_at="2026-07-19T10:12:00Z",
    )

    assert completed.status is transaction.TransactionJournalStatus.COMPLETED
    assert evidence.control_lease_epoch_audit == recovered_record.epoch


def test_target_committed拒绝完整record摘要漂移的acceptance() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence, permit = _public_terminal_input(
        target_committed=True
    )
    tampered_acceptance = dataclasses.replace(
        acceptance,
        control_lease_record_sha256_audit="9" * 64,
    )

    with pytest.raises(TransactionTerminalError):
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.TARGET_COMMITTED,
            final_state=state,
            acceptance=tampered_acceptance,
            serving_fence=fence,
            serve_permit=permit,
            completed_at="2026-07-19T10:12:00Z",
            control_lease_lineage=(record,),
        )


@pytest.mark.parametrize(
    ("mode", "public"),
    (
        (GenerationMode.STEADY, True),
        (GenerationMode.RESTRICTED, False),
    ),
)
def test_baseline_restored覆盖公开与受限两种合法收口(
    mode: GenerationMode,
    public: bool,
) -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    if public:
        attempt, journal, record, proof, state, acceptance, fence, permit = _public_terminal_input(
            target_committed=False
        )
        completed_at = "2026-07-19T10:12:00Z"
    else:
        attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input(mode)
        permit = None
        completed_at = "2026-07-19T10:09:00Z"

    _, evidence = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.BASELINE_RESTORED,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=permit,
        completed_at=completed_at,
        control_lease_lineage=(record,),
    )

    assert evidence.final_mode is mode
    assert evidence.final_serving_generation_id == attempt.baseline_generation_id


def test_complete拒绝空journal与倒退完成时间() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    empty = transaction.create_transaction_journal(
        _reservation(),
        created_at="2026-07-19T10:00:30Z",
    )
    common = {
        "attempt": attempt,
        "outcome": TransactionTerminalOutcome.SAFETY_UNPROVEN,
        "final_state": state,
        "acceptance": acceptance,
        "serving_fence": fence,
        "serve_permit": None,
    }

    with pytest.raises(TransactionTerminalError, match="空 journal"):
        terminal.complete_transaction_journal(
            empty,
            record,
            proof,
            **common,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=(record,),
        )
    with pytest.raises(TransactionTerminalError, match="严格晚于"):
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            **common,
            completed_at=journal.updated_at,
            control_lease_lineage=(record,),
        )


def test_complete拒绝未提交动作错误reservation租约与重复完成() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    incomplete = dataclasses.replace(
        journal,
        actions=journal.actions[:-1],
        updated_at=journal.actions[-2].recorded_at,
    )
    common = {
        "attempt": attempt,
        "outcome": TransactionTerminalOutcome.SAFETY_UNPROVEN,
        "final_state": state,
        "acceptance": acceptance,
        "serving_fence": fence,
        "serve_permit": None,
        "completed_at": "2026-07-19T10:09:00Z",
    }

    with pytest.raises(TransactionTerminalError, match="COMMITTED"):
        terminal.complete_transaction_journal(
            incomplete,
            record,
            proof,
            **common,
            control_lease_lineage=(record,),
        )
    drifted_reservation = dataclasses.replace(
        _reservation(),
        controller_sha256="1" * 64,
    )
    drifted_record, drifted_proof = issue_control_lease(
        drifted_reservation,
        epoch=1,
        token=bytes(range(32, 64)),
        owner="controller-b",
        issued_at="2026-07-19T10:01:00Z",
    )
    with pytest.raises(TransactionTerminalError, match="reservation"):
        terminal.complete_transaction_journal(
            journal,
            drifted_record,
            drifted_proof,
            **common,
            control_lease_lineage=(drifted_record,),
        )
    completed, _ = terminal.complete_transaction_journal(
        journal,
        record,
        proof,
        **common,
        control_lease_lineage=(record,),
    )
    with pytest.raises(TransactionTerminalError, match="活动 journal"):
        terminal.complete_transaction_journal(
            completed,
            record,
            proof,
            **common,
            control_lease_lineage=(record,),
        )


def test终态入口把底层租约与验收绑定异常统一为领域异常() -> None:
    """公开终态边界不能泄漏 fencing 或 acceptance 子域异常。"""
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence, permit = _public_terminal_input(
        target_committed=True
    )
    common = {
        "attempt": attempt,
        "outcome": TransactionTerminalOutcome.TARGET_COMMITTED,
        "final_state": state,
        "acceptance": acceptance,
        "serving_fence": fence,
        "serve_permit": permit,
        "completed_at": "2026-07-19T10:12:00Z",
    }

    invalid_proof = dataclasses.replace(proof, token=bytes(range(1, 33)))
    with pytest.raises(TransactionTerminalError) as lease_error:
        terminal.complete_transaction_journal(
            journal,
            record,
            invalid_proof,
            **common,
            control_lease_lineage=(record,),
        )
    assert type(lease_error.value) is TransactionTerminalError

    invalid_acceptance = dataclasses.replace(
        acceptance,
        serving_fence_token_sha256="8" * 64,
    )
    with pytest.raises(TransactionTerminalError) as acceptance_error:
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            **(common | {"acceptance": invalid_acceptance}),
            control_lease_lineage=(record,),
        )
    assert type(acceptance_error.value) is TransactionTerminalError
