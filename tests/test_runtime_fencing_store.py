"""control lease 持久化与终态清理门禁测试。"""

from __future__ import annotations

import os
import shutil
from dataclasses import fields, replace
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    control_lease_record_sha256,
    encode_control_lease_record,
    issue_control_lease,
)
from codev_platform.runtime_fencing_store import (
    ControlLeaseStore,
    ControlLeaseStoreError,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    write_managed_bytes_atomic,
)
from codev_platform.runtime_recovery_contract import (
    create_recovery_envelope,
)
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_terminal_evidence_path,
    control_lease_history_path,
    control_lease_record_path,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeMutationScope,
    RuntimeStorePolicy,
    RuntimeTerminalCleanupScope,
)
from codev_platform.runtime_transaction_codec import encode_transaction_journal
from codev_platform.runtime_transaction_contract import (
    MAX_TRANSACTION_JOURNAL_BYTES,
    TransactionJournal,
    create_transaction_journal,
)
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore
from codev_platform.runtime_transaction_terminal import complete_transaction_journal
from codev_platform.runtime_transaction_terminal_evidence import (
    TransactionTerminalEvidence,
    TransactionTerminalOutcome,
    encode_transaction_terminal_evidence,
)
from tests.runtime_transaction_terminal_support import (
    _reservation as _terminal_reservation,
    _safety_terminal_input,
)
from tests.runtime_store_race_support import (
    ControlLeaseRaceInput,
    ControlLeaseRaceResult,
    ControlLeaseTransitionCrashInput,
    ControlLeaseTransitionRootSwapInput,
    InitialLeaseRootSwapRaceInput,
    run_initial_root_swap_race,
    run_takeover_retire_race,
)


_POSIX = os.name == "posix"
_PRIVATE_POLICY_BYTES = MAX_TRANSACTION_JOURNAL_BYTES


def test竞争进程输入与队列结果数据对象不携带控制能力字段() -> None:
    """所有子进程输入和队列结果数据对象都只传递公开的可序列化数据。"""
    forbidden_names = ("proof", "token", "store", "scope", "fd")
    transfer_types = (
        ControlLeaseRaceInput,
        InitialLeaseRootSwapRaceInput,
        ControlLeaseTransitionCrashInput,
        ControlLeaseTransitionRootSwapInput,
        ControlLeaseRaceResult,
    )

    for transfer_type in transfer_types:
        field_names = {field.name.lower() for field in fields(transfer_type)}
        for forbidden_name in forbidden_names:
            assert all(forbidden_name not in field_name for field_name in field_names)


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _lease_store_input(
    tmp_path: Path,
    *,
    reservation: AttemptReservation | None = None,
    journal_created_at: str | None = None,
) -> tuple[
    ControlLeaseStore,
    RuntimeTransactionBootstrapStore,
    Path,
    AttemptReservation,
    TransactionJournal,
]:
    root = tmp_path / "runtime"
    root.mkdir(parents=True)
    actual_reservation = _reservation() if reservation is None else reservation
    created_at = journal_created_at or actual_reservation.created_at
    journal = create_transaction_journal(actual_reservation, created_at=created_at)
    envelope = create_recovery_envelope(
        actual_reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(actual_reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    return ControlLeaseStore(policy), bootstrap, root, actual_reservation, journal


def _initial_owner() -> dict[str, object]:
    return {
        "owner": "controller-a",
        "token": bytes(range(32)),
        "issued_at": "2026-07-21T00:00:01Z",
    }


def _recovery_owner() -> dict[str, object]:
    return {
        "owner": "recovery-a",
        "token": bytes(range(32, 64)),
        "issued_at": "2026-07-21T00:00:02Z",
    }


def _managed_policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(
        mode=0o600,
        require_uid=os.geteuid(),
        max_bytes=_PRIVATE_POLICY_BYTES,
    )


def _persist_terminal_records(
    root: Path,
    journal: TransactionJournal,
    evidence: TransactionTerminalEvidence,
) -> None:
    write_managed_bytes_atomic(
        attempt_journal_path(root, journal.attempt_id),
        encode_transaction_journal(journal),
        root=root,
        policy=_managed_policy(),
    )
    write_managed_bytes_atomic(
        attempt_terminal_evidence_path(root, evidence.attempt_id),
        encode_transaction_terminal_evidence(evidence),
        root=root,
        policy=_managed_policy(),
    )


def _terminal_store_input(
    tmp_path: Path,
) -> tuple[
    ControlLeaseStore,
    Path,
    ControlLeaseSnapshot,
    ControlLeaseProof,
    TransactionJournal,
    TransactionTerminalEvidence,
    AttemptReservation,
]:
    attempt, active, expected_record, expected_proof, state, acceptance, fence = (
        _safety_terminal_input()
    )
    reservation = _terminal_reservation()
    store, _bootstrap, root, persisted_reservation, _genesis = _lease_store_input(
        tmp_path,
        reservation=reservation,
        journal_created_at=active.created_at,
    )
    initial, proof = store.acquire_initial(
        owner=expected_record.owner,
        token=expected_proof.token,
        issued_at=expected_record.issued_at,
    )
    assert persisted_reservation == reservation
    assert initial.record == expected_record
    assert proof == expected_proof
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
    _persist_terminal_records(root, completed, evidence)
    return store, root, initial, proof, completed, evidence, reservation


def test控制租约快照拒绝与规范记录不一致的摘要() -> None:
    record, _proof = issue_control_lease(
        _reservation(),
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-21T00:00:01Z",
    )

    with pytest.raises(ValueError, match="摘要"):
        ControlLeaseSnapshot(record=record, sha256="1" * 64)

    assert (
        ControlLeaseSnapshot(
            record=record,
            sha256=control_lease_record_sha256(record),
        ).record
        == record
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test接管先冻结历史再切换current并可重建完整lineage(tmp_path: Path) -> None:
    store, _bootstrap, _root, _reservation_value, _journal = _lease_store_input(tmp_path)
    initial, _ = store.acquire_initial(**_initial_owner())

    successor, _ = store.take_over(initial, **_recovery_owner())

    assert store.load_current() == successor
    assert store.load_active_lineage(successor.record) == (
        initial.record,
        successor.record,
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test历史缺失或字节漂移时闭锁失败(tmp_path: Path) -> None:
    store, _bootstrap, root, _reservation_value, _journal = _lease_store_input(tmp_path)
    initial, _ = store.acquire_initial(**_initial_owner())
    successor, _ = store.take_over(initial, **_recovery_owner())
    predecessor_path = control_lease_history_path(
        root,
        initial.record.attempt_id,
        initial.sha256,
    )
    predecessor_path.unlink()

    with pytest.raises(ControlLeaseStoreError, match="历史|history|lineage"):
        store.load_active_lineage(successor.record)

    store, _bootstrap, root, _reservation_value, _journal = _lease_store_input(
        tmp_path / "drift",
    )
    initial, _ = store.acquire_initial(**_initial_owner())
    history_path = control_lease_history_path(root, initial.record.attempt_id, initial.sha256)
    drifted = replace(initial.record, owner="controller-b")
    write_managed_bytes_atomic(
        history_path,
        encode_control_lease_record(drifted),
        root=root,
        policy=_managed_policy(),
    )

    with pytest.raises(ControlLeaseStoreError, match="历史|摘要|完整性"):
        store.load_current()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test错误或旧proof不能进入活动mutation(tmp_path: Path) -> None:
    store, _bootstrap, _root, _reservation_value, _journal = _lease_store_input(tmp_path)
    initial, proof = store.acquire_initial(**_initial_owner())
    wrong = ControlLeaseProof(
        attempt_id=initial.record.attempt_id,
        epoch=initial.record.epoch,
        token=b"z" * 32,
    )

    with pytest.raises(ControlLeaseStoreError, match="proof|token"):
        with store.mutation(wrong):
            pass

    store.take_over(initial, **_recovery_owner())

    with pytest.raises(ControlLeaseStoreError, match="proof|当前|活动"):
        with store.mutation(proof):
            pass


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test退休严格拒绝缺失或与内存不一致的持久终态(tmp_path: Path) -> None:
    store, root, initial, proof, completed, evidence, _reservation_value = _terminal_store_input(
        tmp_path,
    )
    attempt_terminal_evidence_path(root, evidence.attempt_id).unlink()

    with pytest.raises(ControlLeaseStoreError, match="终态|evidence|加载"):
        store.retire_current(
            proof,
            completed,
            evidence,
            retired_at="2026-07-19T10:10:00Z",
        )

    _persist_terminal_records(root, completed, evidence)
    drifted = replace(completed, terminal_evidence_sha256="1" * 64)

    with pytest.raises(ControlLeaseStoreError, match="持久|不一致|journal"):
        store.retire_current(
            proof,
            drifted,
            evidence,
            retired_at="2026-07-19T10:10:00Z",
        )

    assert store.load_current() == initial


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test接管先胜时旧proof失效而新epoch可退休(tmp_path: Path) -> None:
    store, _root, initial, proof, completed, evidence, _reservation_value = _terminal_store_input(
        tmp_path,
    )
    successor, successor_proof = store.take_over(
        initial,
        owner="recovery-a",
        token=bytes(range(32, 64)),
        issued_at="2026-07-19T10:09:30Z",
    )

    with pytest.raises(ControlLeaseStoreError, match="proof|当前|活动"):
        store.retire_current(
            proof,
            completed,
            evidence,
            retired_at="2026-07-19T10:10:00Z",
        )

    assert store.load_current() == successor
    retired = store.retire_current(
        successor_proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )
    assert retired.record.status.value == "retired"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test退休先胜后拒绝接管且清理门禁拒绝漂移tombstone(tmp_path: Path) -> None:
    store, root, initial, proof, completed, evidence, _reservation_value = _terminal_store_input(
        tmp_path,
    )
    retired = store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )

    with pytest.raises(ControlLeaseStoreError, match="同一 attempt|初始"):
        store.acquire_initial(**_initial_owner())
    with pytest.raises(ControlLeaseStoreError, match="当前|活动|CAS"):
        store.take_over(
            initial,
            owner="recovery-a",
            token=bytes(range(32, 64)),
            issued_at="2026-07-19T10:11:00Z",
        )

    with pytest.raises(ControlLeaseStoreError, match="proof|活动|当前"):
        with store.mutation(proof):
            pass
    with store.terminal_cleanup(retired) as scope:
        assert isinstance(scope, RuntimeTerminalCleanupScope)
        assert scope.snapshot == retired

    history_path = control_lease_history_path(
        root,
        retired.record.attempt_id,
        retired.sha256,
    )
    history_path.unlink()

    with pytest.raises(ControlLeaseStoreError, match="历史|history|tombstone"):
        with store.terminal_cleanup(retired):
            pass


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动scope仅暴露同一绑定根且离开后失效(tmp_path: Path) -> None:
    store, _bootstrap, root, _reservation_value, _journal = _lease_store_input(tmp_path)
    _initial, proof = store.acquire_initial(**_initial_owner())

    with store.mutation(proof) as scope:
        assert isinstance(scope, RuntimeMutationScope)
        assert scope.snapshot.record.status.value == "active"
        assert scope.bound_root.path == root
        bound_root = scope.bound_root

    with pytest.raises(ValueError, match="关闭|失效"):
        bound_root.verify_visible()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test不同attempt的tombstone允许签发新的初始租约(tmp_path: Path) -> None:
    store, root, _initial, proof, completed, evidence, _reservation_value = _terminal_store_input(
        tmp_path,
    )
    store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )
    next_reservation = replace(
        _reservation(),
        attempt_id="e" * 32,
        plan_sha256="e" * 64,
        created_at="2026-07-21T00:00:00Z",
    )
    next_journal = create_transaction_journal(
        next_reservation,
        created_at=next_reservation.created_at,
    )
    next_envelope = create_recovery_envelope(
        next_reservation,
        next_journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=next_reservation.created_at,
    )
    bootstrap = RuntimeTransactionBootstrapStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    bootstrap.reserve_attempt(next_reservation)
    bootstrap.write_journal_genesis_once(next_journal)
    bootstrap.write_envelope_once(next_envelope)
    active_recovery_envelope_path(root).unlink()
    bootstrap.publish_active_envelope_if_absent(next_envelope)

    successor, _ = store.acquire_initial(
        owner="controller-b",
        token=b"q" * 32,
        issued_at="2026-07-21T00:00:01Z",
    )

    assert successor.record.attempt_id == next_reservation.attempt_id


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test接管与退休跨进程竞争仅一方赢且不留下孤儿history(tmp_path: Path) -> None:
    store, root, initial, _proof, completed, evidence, _reservation_value = _terminal_store_input(
        tmp_path,
    )

    results = run_takeover_retire_race(
        ControlLeaseRaceInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=initial,
            completed=completed,
            evidence=evidence,
        ),
    )

    assert [result.outcome for result in results].count("success") == 1
    assert [result.outcome for result in results].count("expected_conflict") == 1
    winner = next(result for result in results if result.outcome == "success")
    assert winner.record_sha256 is not None
    current = store.load_current()
    assert current is not None
    assert current.sha256 == winner.record_sha256
    history_directory = control_lease_history_path(
        root,
        initial.record.attempt_id,
        initial.sha256,
    ).parent
    assert {path.stem for path in history_directory.glob("*.json")} == {
        initial.sha256,
        winner.record_sha256,
    }


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test两进程根替换后均闭锁且替换根不产生lease文件(tmp_path: Path) -> None:
    _store, _bootstrap, root, _reservation_value, _journal = _lease_store_input(tmp_path)
    replacement = tmp_path / "replacement"
    shutil.copytree(root, replacement)

    results = run_initial_root_swap_race(
        InitialLeaseRootSwapRaceInput(
            root=str(root),
            replacement=str(replacement),
            owner_uid=os.geteuid(),
        ),
    )

    assert {result.outcome for result in results} == {"expected_root_drift"}
    assert not control_lease_record_path(root).exists()
    assert not (root / "control-leases").exists()
