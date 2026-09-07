from __future__ import annotations

import dataclasses
import importlib

import pytest

from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_fencing import recover_control_lease
from codev_platform.runtime_generation_acceptance import acceptance_record_sha256
from codev_platform.runtime_generation_state import GenerationMode, generation_state_sha256
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalError,
    TransactionTerminalOutcome,
    transaction_terminal_evidence_sha256,
)
from codev_platform import runtime_transaction_contract as transaction
from tests.runtime_transaction_terminal_support import (
    _recovered_final_action_input,
    _reservation,
    _safety_terminal_input,
)


def test_complete_lease必须是journal最后动作审计的合法后继() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    (
        attempt,
        journal,
        record,
        proof,
        recovered_record,
        _,
        state,
        acceptance,
        fence,
    ) = _recovered_final_action_input()

    with pytest.raises(TransactionTerminalError, match="lease|lineage|epoch"):
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
            control_lease_lineage=(record, recovered_record),
            completed_at="2026-07-19T10:09:00Z",
        )


def test_complete允许合法recovery_lineage并绑定完整completion_lease摘要() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    (
        attempt,
        journal,
        record,
        _,
        recovered_record,
        recovered_proof,
        state,
        acceptance,
        fence,
    ) = _recovered_final_action_input()

    completed, evidence = terminal.complete_transaction_journal(
        journal,
        recovered_record,
        recovered_proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        control_lease_lineage=(record, recovered_record),
        completed_at="2026-07-19T10:09:00Z",
    )

    assert completed.status is transaction.TransactionJournalStatus.COMPLETED
    assert evidence.completion_control_lease_sha256 == canonical_sha256(recovered_record)


def test_complete时间必须晚于全部typed事实() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()
    late_acceptance = dataclasses.replace(
        acceptance,
        accepted_at="2026-07-19T10:10:00Z",
    )
    late_fence = dataclasses.replace(
        fence,
        issued_at="2026-07-19T10:10:00Z",
    )

    for changed_acceptance, changed_fence in (
        (late_acceptance, fence),
        (acceptance, late_fence),
    ):
        with pytest.raises(TransactionTerminalError, match="时间|晚于"):
            terminal.complete_transaction_journal(
                journal,
                record,
                proof,
                attempt=attempt,
                outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
                final_state=state,
                acceptance=changed_acceptance,
                serving_fence=changed_fence,
                serve_permit=None,
                completed_at="2026-07-19T10:09:00Z",
                control_lease_lineage=(record,),
            )

    late_state = dataclasses.replace(
        state,
        updated_at="2026-07-19T10:10:00Z",
    )
    late_state_actions = tuple(
        dataclasses.replace(action, after_sha256=generation_state_sha256(late_state))
        if action.step_sequence == 2
        else action
        for action in journal.actions
    )
    late_state_journal = dataclasses.replace(
        journal,
        actions=late_state_actions,
        updated_at=late_state_actions[-1].recorded_at,
    )
    with pytest.raises(TransactionTerminalError, match="时间|晚于"):
        terminal.complete_transaction_journal(
            late_state_journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
            final_state=late_state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=(record,),
        )


def test验证已持久终态时仍校验全部typed事实时间() -> None:
    """重启后的 context 校验不能跳过 completed_at 的事实时间边界。"""
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
    late_acceptance = dataclasses.replace(
        acceptance,
        accepted_at="2026-07-19T10:10:00Z",
    )
    late_evidence = dataclasses.replace(
        evidence,
        acceptance_sha256=acceptance_record_sha256(late_acceptance),
    )
    late_completed = dataclasses.replace(
        completed,
        terminal_evidence_sha256=transaction_terminal_evidence_sha256(late_evidence),
    )

    with pytest.raises(TransactionTerminalError, match="时间|晚于"):
        terminal.verify_transaction_terminal_context(
            late_completed,
            late_evidence,
            attempt=attempt,
            final_state=state,
            acceptance=late_acceptance,
            serving_fence=fence,
            serve_permit=None,
            current_lease=record,
            control_lease_lineage=(record,),
        )


def test完成证据拒绝落在旧lease后继接管之后() -> None:
    """完成证据锚定旧 lease 时，后继 lease 必须在完成之后才可签发。"""
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
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=2,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-19T10:08:30Z",
    )

    with pytest.raises(TransactionTerminalError, match="后继|lineage"):
        terminal.retire_control_lease(
            recovered_record,
            recovered_proof,
            completed_journal=completed,
            terminal_evidence=evidence,
            attempt=attempt,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            control_lease_lineage=(record, recovered_record),
            retired_at="2026-07-19T10:10:00Z",
        )


def test终态拒绝旧lease在后继接管后记录的动作() -> None:
    """每条 journal action 都必须处于其控制锚点的有效时间窗口。"""
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, _, state, acceptance, fence = _safety_terminal_input()
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=2,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-19T10:05:30Z",
    )

    with pytest.raises(TransactionTerminalError, match="后继|lineage"):
        terminal.complete_transaction_journal(
            journal,
            recovered_record,
            recovered_proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            control_lease_lineage=(record, recovered_record),
            completed_at="2026-07-19T10:09:00Z",
        )


def test_危险终态若保留代际绑定必须匹配typed_attempt() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input(
        GenerationMode.RESTRICTED
    )
    drifted_state = dataclasses.replace(
        state,
        desired_generation_id="1" * 64,
        rollback_generation_id="2" * 64,
    )
    actions = tuple(
        dataclasses.replace(action, after_sha256=generation_state_sha256(drifted_state))
        if action.step_sequence == 2
        else action
        for action in journal.actions
    )
    drifted_journal = dataclasses.replace(
        journal,
        actions=actions,
        updated_at=actions[-1].recorded_at,
    )

    with pytest.raises(TransactionTerminalError, match="target|baseline|代际"):
        terminal.complete_transaction_journal(
            drifted_journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.BASELINE_RESTORED,
            final_state=drifted_state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=(record,),
        )


def test_complete拒绝伪造outcome与漂移acceptance() -> None:
    terminal = importlib.import_module("codev_platform.runtime_transaction_terminal")
    attempt, journal, record, proof, state, acceptance, fence = _safety_terminal_input()

    with pytest.raises(TransactionTerminalError, match="target_committed"):
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.TARGET_COMMITTED,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=(record,),
        )
    forged_acceptance = dataclasses.replace(
        acceptance,
        health_proof_sha256="f" * 64,
    )
    with pytest.raises(TransactionTerminalError, match="acceptance"):
        terminal.complete_transaction_journal(
            journal,
            record,
            proof,
            attempt=attempt,
            outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
            final_state=state,
            acceptance=forged_acceptance,
            serving_fence=fence,
            serve_permit=None,
            completed_at="2026-07-19T10:09:00Z",
            control_lease_lineage=(record,),
        )


def test_terminal_evidence_codec拒绝重复字段额外字段与超限载荷() -> None:
    evidence_codec = importlib.import_module("codev_platform.runtime_transaction_terminal_evidence")
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
    payload = evidence_codec.encode_transaction_terminal_evidence(evidence)
    duplicate = payload.replace(
        b'{"acceptance_sha256":',
        b'{"schema_version":1,"acceptance_sha256":',
        1,
    )
    extra = payload[:-1] + b',"unexpected":true}'

    for malicious in (duplicate, extra, b"{" + b" " * 32_768 + b"}"):
        with pytest.raises(evidence_codec.TransactionTerminalError):
            evidence_codec.decode_transaction_terminal_evidence(malicious)


def test_retire允许完成后同attempt更高epoch接管并拒绝篡改证据() -> None:
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
    recovered_record, recovered_proof = recover_control_lease(
        record,
        _reservation(),
        epoch=2,
        token=bytes(range(32, 64)),
        owner="recovery-a",
        issued_at="2026-07-19T10:09:30Z",
    )

    with pytest.raises(TransactionTerminalError, match="lease|lineage|谱系"):
        terminal.retire_control_lease(
            recovered_record,
            recovered_proof,
            completed_journal=completed,
            terminal_evidence=evidence,
            attempt=attempt,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            control_lease_lineage=(recovered_record,),
            retired_at="2026-07-19T10:10:00Z",
        )

    retired = terminal.retire_control_lease(
        recovered_record,
        recovered_proof,
        completed_journal=completed,
        terminal_evidence=evidence,
        attempt=attempt,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        control_lease_lineage=(record, recovered_record),
        retired_at="2026-07-19T10:10:00Z",
    )
    assert retired.epoch == 2

    forged = dataclasses.replace(evidence, final_state_sha256="f" * 64)
    with pytest.raises(TransactionTerminalError):
        terminal.retire_control_lease(
            recovered_record,
            recovered_proof,
            completed_journal=completed,
            terminal_evidence=forged,
            attempt=attempt,
            final_state=state,
            acceptance=acceptance,
            serving_fence=fence,
            serve_permit=None,
            control_lease_lineage=(record, recovered_record),
            retired_at="2026-07-19T10:10:00Z",
        )
