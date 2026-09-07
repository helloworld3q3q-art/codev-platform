"""control lease store 的前驱、终态和 tombstone 对抗测试。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    encode_attempt_reservation,
)
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    control_lease_record_sha256,
    encode_control_lease_record,
    issue_control_lease,
)
from codev_platform.runtime_control_lease_persistence import (
    ControlLeasePersistence,
    ControlLeasePersistenceError,
)
from codev_platform.runtime_fencing_store import (
    ControlLeaseStore,
    ControlLeaseStoreError,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    read_managed_bytes_at,
    write_managed_bytes_atomic,
    write_managed_bytes_atomic_at,
)
from codev_platform.runtime_recovery_contract import (
    create_recovery_envelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_reservation_path,
    attempt_terminal_evidence_path,
    control_lease_history_path,
    control_lease_record_path,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeStorePolicy,
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


_POSIX = os.name == "posix"


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _managed_policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(
        mode=0o600,
        require_uid=os.geteuid(),
        max_bytes=MAX_TRANSACTION_JOURNAL_BYTES,
    )


def _lease_store_input(
    tmp_path: Path,
    *,
    reservation: AttemptReservation | None = None,
    journal_created_at: str | None = None,
) -> tuple[
    ControlLeaseStore,
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
    return ControlLeaseStore(policy), root, actual_reservation, journal


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


def _persist_terminal(
    root: Path,
    completed: TransactionJournal,
    evidence: TransactionTerminalEvidence,
) -> None:
    write_managed_bytes_atomic(
        attempt_journal_path(root, completed.attempt_id),
        encode_transaction_journal(completed),
        root=root,
        policy=_managed_policy(),
    )
    write_managed_bytes_atomic(
        attempt_terminal_evidence_path(root, evidence.attempt_id),
        encode_transaction_terminal_evidence(evidence),
        root=root,
        policy=_managed_policy(),
    )


def _drift_active_bootstrap(root: Path, drift_kind: str) -> None:
    """模拟 lease 签发后活动 bootstrap 被删除或替换的持久化漂移。"""
    if drift_kind == "活动 envelope 缺失":
        active_recovery_envelope_path(root).unlink()
        return
    if drift_kind == "活动 envelope 替换为不同 attempt":
        replacement = replace(
            _reservation(),
            attempt_id="e" * 32,
            plan_sha256="e" * 64,
        )
        journal = create_transaction_journal(
            replacement,
            created_at=replacement.created_at,
        )
        envelope = create_recovery_envelope(
            replacement,
            journal,
            controller_root_relative="controller",
            interpreter_relative="controller/bin/python",
            interpreter_sha256="d" * 64,
            transaction_store_id="runtime-bootstrap",
            created_at=replacement.created_at,
        )
        bootstrap = RuntimeTransactionBootstrapStore(
            RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
        )
        bootstrap.reserve_attempt(replacement)
        bootstrap.write_journal_genesis_once(journal)
        bootstrap.write_envelope_once(envelope)
        write_managed_bytes_atomic(
            active_recovery_envelope_path(root),
            encode_recovery_envelope(envelope),
            root=root,
            policy=_managed_policy(),
        )
        return
    if drift_kind == "reservation 漂移":
        drifted = replace(_terminal_reservation(), plan_sha256="1" * 64)
        write_managed_bytes_atomic(
            attempt_reservation_path(root, drifted.attempt_id),
            encode_attempt_reservation(drifted),
            root=root,
            policy=_managed_policy(),
        )
        return
    raise AssertionError(f"未知活动 bootstrap 漂移类型：{drift_kind}")


def _run_valid_proof_operation(
    store: ControlLeaseStore,
    operation: str,
    proof: ControlLeaseProof,
    completed: TransactionJournal,
    evidence: TransactionTerminalEvidence,
) -> None:
    """以未过期 proof 执行唯一待验证的活动控制入口。"""
    if operation == "mutation":
        with store.mutation(proof):
            return None
    if operation == "retire":
        store.retire_current(
            proof,
            completed,
            evidence,
            retired_at="2026-07-19T10:10:00Z",
        )
        return None
    raise AssertionError(f"未知控制操作：{operation}")


def _terminal_store_input(
    tmp_path: Path,
) -> tuple[
    ControlLeaseStore,
    Path,
    ControlLeaseSnapshot,
    ControlLeaseProof,
    TransactionJournal,
    TransactionTerminalEvidence,
]:
    attempt, active, expected_record, expected_proof, state, acceptance, fence = (
        _safety_terminal_input()
    )
    store, root, _reservation_value, _journal = _lease_store_input(
        tmp_path,
        reservation=_terminal_reservation(),
        journal_created_at=active.created_at,
    )
    initial, proof = store.acquire_initial(
        owner=expected_record.owner,
        token=expected_proof.token,
        issued_at=expected_record.issued_at,
    )
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
    _persist_terminal(root, completed, evidence)
    return store, root, initial, proof, completed, evidence


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "drift_kind",
    (
        "活动 envelope 缺失",
        "活动 envelope 替换为不同 attempt",
        "reservation 漂移",
    ),
)
@pytest.mark.parametrize("operation", ("mutation", "retire"))
def test有效旧proof在活动bootstrap漂移后不能授权写入(
    tmp_path: Path,
    drift_kind: str,
    operation: str,
) -> None:
    """current/proof 本身有效时也必须重新绑定活动 bootstrap 真值。"""
    store, root, initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    _drift_active_bootstrap(root, drift_kind)

    with pytest.raises(ControlLeaseStoreError, match="活动|bootstrap|context|绑定"):
        _run_valid_proof_operation(store, operation, proof, completed, evidence)

    assert store.load_current() == initial


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testcurrent_cas拒绝发布未冻结history的下一个记录(tmp_path: Path) -> None:
    """持久层不能把尚无不可变 history 的记录发布为公开 current。"""
    root = tmp_path / "runtime"
    root.mkdir()
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(policy)
    record, _proof = issue_control_lease(
        _reservation(),
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-21T00:00:01Z",
    )

    with policy.root_binding.bind() as bound_root:
        with pytest.raises(ControlLeasePersistenceError, match="history|历史"):
            persistence.compare_and_swap_current(bound_root, None, record)

    assert not control_lease_record_path(root).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动scope退出后实际at读取拒绝关闭的根租约(tmp_path: Path) -> None:
    """scope 离开临界区后不能再把其根租约传给任何受管 I/O。"""
    store, root, _initial, proof, _completed, _evidence = _terminal_store_input(tmp_path)

    with store.mutation(proof) as scope:
        expired_root = scope.bound_root

    with pytest.raises(RuntimeRootBindingError, match="关闭|失效"):
        read_managed_bytes_at(
            control_lease_record_path(root),
            root=expired_root,
            policy=_managed_policy(),
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test终态清理scope退出后实际at读取拒绝关闭的根租约(tmp_path: Path) -> None:
    """终态 scope 也只在清理临界区存活，不能泄漏为长期文件能力。"""
    store, root, _initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    retired = store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )

    with store.terminal_cleanup(retired) as scope:
        expired_root = scope.bound_root

    with pytest.raises(RuntimeRootBindingError, match="关闭|失效"):
        read_managed_bytes_at(
            control_lease_record_path(root),
            root=expired_root,
            policy=_managed_policy(),
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test租约持久化拒绝其他策略签发的根租约(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    foreign_policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(policy)

    with foreign_policy.root_binding.bind() as foreign_root:
        with pytest.raises(ControlLeasePersistenceError, match="其他|绑定|根租约"):
            persistence.load_current_or_none(foreign_root)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test租约持久化拒绝历史载荷跨尝试目录复用(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(policy)
    record, _proof = issue_control_lease(
        _reservation(),
        epoch=1,
        token=bytes(range(32)),
        owner="controller-a",
        issued_at="2026-07-21T00:00:01Z",
    )
    record_sha256 = control_lease_record_sha256(record)
    source_path = control_lease_history_path(
        root,
        record.attempt_id,
        record_sha256,
    )
    reused_attempt_id = "e" * 32
    reused_path = control_lease_history_path(
        root,
        reused_attempt_id,
        record_sha256,
    )

    with policy.root_binding.bind() as bound_root:
        persistence.freeze_history(bound_root, record)
        payload = read_managed_bytes_at(
            source_path,
            root=bound_root,
            policy=_managed_policy(),
        )
        write_managed_bytes_atomic_at(
            reused_path,
            payload,
            root=bound_root,
            policy=_managed_policy(),
        )

        with pytest.raises(ControlLeasePersistenceError, match="attempt|身份|history"):
            persistence.load_history(bound_root, reused_attempt_id, record_sha256)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test缺失活动前驱历史使接管和mutation均闭锁(tmp_path: Path) -> None:
    store, root, _reservation_value, _journal = _lease_store_input(tmp_path)
    initial, _initial_proof = store.acquire_initial(**_initial_owner())
    successor, successor_proof = store.take_over(initial, **_recovery_owner())
    control_lease_history_path(root, initial.record.attempt_id, initial.sha256).unlink()

    with pytest.raises(ControlLeaseStoreError, match="历史|lineage"):
        store.take_over(
            successor,
            owner="recovery-b",
            token=b"q" * 32,
            issued_at="2026-07-21T00:00:03Z",
        )
    with pytest.raises(ControlLeaseStoreError, match="历史|lineage"):
        with store.mutation(successor_proof):
            pass


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test缺失退休前驱历史使清理与新初始租约均闭锁(tmp_path: Path) -> None:
    store, root, initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    retired = store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )
    control_lease_history_path(root, initial.record.attempt_id, initial.sha256).unlink()

    with pytest.raises(ControlLeaseStoreError, match="历史|lineage|tombstone"):
        with store.terminal_cleanup(retired):
            pass

    next_reservation = replace(
        _reservation(),
        attempt_id="e" * 32,
        plan_sha256="e" * 64,
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
    write_managed_bytes_atomic(
        active_recovery_envelope_path(root),
        encode_recovery_envelope(next_envelope),
        root=root,
        policy=_managed_policy(),
    )

    with pytest.raises(ControlLeaseStoreError, match="历史|lineage|tombstone"):
        store.acquire_initial(
            owner="controller-b",
            token=b"q" * 32,
            issued_at="2026-07-21T00:00:01Z",
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    ("artifact", "payload_kind"),
    (
        ("journal", "截断"),
        ("journal", "未知字段"),
        ("evidence", "截断"),
        ("evidence", "未知字段"),
    ),
)
def test退休拒绝截断或未知字段的持久终态(
    tmp_path: Path,
    artifact: str,
    payload_kind: str,
) -> None:
    store, root, _initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    path = (
        attempt_journal_path(root, completed.attempt_id)
        if artifact == "journal"
        else attempt_terminal_evidence_path(root, evidence.attempt_id)
    )
    canonical = (
        encode_transaction_journal(completed)
        if artifact == "journal"
        else encode_transaction_terminal_evidence(evidence)
    )
    payload = b"{" if payload_kind == "截断" else canonical[:-1] + b',"unknown":1}'
    write_managed_bytes_atomic(path, payload, root=root, policy=_managed_policy())

    with pytest.raises(ControlLeaseStoreError, match="终态|严格|规范"):
        store.retire_current(
            proof,
            completed,
            evidence,
            retired_at="2026-07-19T10:10:00Z",
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("drift_kind", ("摘要", "身份"))
def test退休拒绝持久终态摘要或身份漂移(
    tmp_path: Path,
    drift_kind: str,
) -> None:
    store, root, _initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    if drift_kind == "摘要":
        drifted_completed = replace(completed, terminal_evidence_sha256="1" * 64)
        drifted_evidence = evidence
    else:
        drifted_completed = completed
        drifted_evidence = replace(evidence, attempt_id="e" * 32)
    _persist_terminal(root, drifted_completed, drifted_evidence)

    with pytest.raises(ControlLeaseStoreError, match="终态|绑定|摘要|身份"):
        store.retire_current(
            proof,
            drifted_completed,
            drifted_evidence,
            retired_at="2026-07-19T10:10:00Z",
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test退休tombstone的retired_from漂移不能开启清理(tmp_path: Path) -> None:
    store, root, _initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    retired = store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )
    drifted_record = replace(retired.record, retired_from_sha256="1" * 64)
    drifted = ControlLeaseSnapshot(
        record=drifted_record,
        sha256=control_lease_record_sha256(drifted_record),
    )
    payload = encode_control_lease_record(drifted.record)
    write_managed_bytes_atomic(
        control_lease_history_path(root, drifted.record.attempt_id, drifted.sha256),
        payload,
        root=root,
        policy=_managed_policy(),
    )
    write_managed_bytes_atomic(
        control_lease_record_path(root),
        payload,
        root=root,
        policy=_managed_policy(),
    )

    with pytest.raises(ControlLeaseStoreError, match="历史|tombstone|退休"):
        with store.terminal_cleanup(drifted):
            pass
