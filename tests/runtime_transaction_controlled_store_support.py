"""受控 transaction post-lease 测试的共享真实夹具。"""

from __future__ import annotations

import os
from pathlib import Path

from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_recovery_contract import create_recovery_envelope
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_controlled_store import RuntimeTransactionStore
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    create_transaction_journal,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore
from codev_platform.runtime_transaction_terminal import complete_transaction_journal
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    TransactionTerminalOutcome,
)
from tests.runtime_transaction_terminal_support import (
    _reservation,
    _safety_terminal_input,
)


def build_terminal_store_input(
    tmp_path: Path,
) -> tuple[
    RuntimeTransactionStore,
    ControlLeaseStore,
    Path,
    TransactionJournal,
    TransactionJournal,
    TransactionTerminalEvidence,
    ControlLeaseProof,
]:
    """用真实 bootstrap 与 control gate 准备 evidence-first 写入边界。"""
    root = tmp_path / "runtime"
    root.mkdir()
    attempt, active, expected_record, expected_proof, state, acceptance, fence = (
        _safety_terminal_input()
    )
    reservation = _reservation()
    genesis = create_transaction_journal(reservation, created_at=active.created_at)
    envelope = create_recovery_envelope(
        reservation,
        genesis,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-postlease",
        created_at=active.created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(genesis)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    gate = ControlLeaseStore(policy)
    initial, proof = gate.acquire_initial(
        owner=expected_record.owner,
        token=expected_proof.token,
        issued_at=expected_record.issued_at,
    )
    assert initial.record == expected_record
    assert proof == expected_proof
    store = RuntimeTransactionStore(policy, gate)
    store.write_attempt_once(attempt, proof=proof)
    store.write_journal(
        active,
        proof=proof,
        expected_sha256=transaction_journal_sha256(genesis),
    )
    completed, evidence = complete_transaction_journal(
        active,
        initial.record,
        proof,
        attempt=attempt,
        outcome=TransactionTerminalOutcome.SAFETY_UNPROVEN,
        final_state=state,
        acceptance=acceptance,
        serving_fence=fence,
        serve_permit=None,
        control_lease_lineage=(initial.record,),
        completed_at="2026-07-19T10:09:00Z",
    )
    return store, gate, root, active, completed, evidence, proof
