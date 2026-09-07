"""control lease pending transition 的崩溃收敛回归。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_fencing_store import (
    ControlLeaseStore,
    ControlLeaseStoreError,
)
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from codev_platform.runtime_fencing import (
    ControlLeaseProof,
    recover_control_lease,
)
from codev_platform.runtime_control_lease_persistence import (
    ControlLeasePersistence,
    ControlLeasePersistenceError,
)
from codev_platform.runtime_control_lease_transition import (
    ControlLeaseTransitionIntent,
    ControlLeaseTransitionKind,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    write_managed_bytes_atomic,
)
from codev_platform.runtime_recovery_contract import (
    create_recovery_envelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_terminal_evidence_path,
    control_lease_history_path,
    control_lease_pending_transition_path,
    control_lease_record_path,
    deployment_lock_at,
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
from tests.runtime_store_race_support import (
    ControlLeaseTransitionCrashInput,
    ControlLeaseTransitionRootSwapInput,
    run_control_lease_transition_crash,
    run_takeover_transition_crash,
    run_takeover_transition_root_swap,
)
from tests import runtime_store_race_support
from tests.runtime_transaction_terminal_support import (
    _reservation as _terminal_reservation,
    _safety_terminal_input,
)


_POSIX = os.name == "posix"


def _pending_path(root: Path) -> Path:
    """固定 pending 必须是运行时根的单一叶子。"""
    return control_lease_pending_transition_path(root)


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
) -> tuple[ControlLeaseStore, Path, ControlLeaseSnapshot, ControlLeaseProof]:
    root = tmp_path / "runtime"
    root.mkdir()
    reservation = _reservation()
    journal = create_transaction_journal(reservation, created_at=reservation.created_at)
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=reservation.created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    store = ControlLeaseStore(policy)
    initial, proof = store.acquire_initial(
        owner="controller-a",
        token=bytes(range(32)),
        issued_at="2026-07-21T00:00:01Z",
    )
    return store, root, initial, proof


def _history_digests(root: Path, snapshot: ControlLeaseSnapshot) -> set[str]:
    directory = control_lease_history_path(
        root,
        snapshot.record.attempt_id,
        snapshot.sha256,
    ).parent
    return {path.stem for path in directory.glob("*.json")}


def _managed_policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(
        mode=0o600,
        require_uid=os.geteuid(),
        max_bytes=MAX_TRANSACTION_JOURNAL_BYTES,
    )


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


def _takeover_intent(initial: ControlLeaseSnapshot) -> ControlLeaseTransitionIntent:
    """构造仅供 pending 门禁回归使用的唯一合法接管候选。"""
    successor, _ = recover_control_lease(
        initial.record,
        _reservation(),
        epoch=initial.record.epoch + 1,
        token=bytes(range(32, 64)),
        owner="pending-reader",
        issued_at="2026-07-21T00:00:02Z",
    )
    return ControlLeaseTransitionIntent(
        schema_version=1,
        kind=ControlLeaseTransitionKind.TAKEOVER,
        expected_record=initial.record,
        next_record=successor,
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
]:
    attempt, active, expected_record, expected_proof, state, acceptance, fence = (
        _safety_terminal_input()
    )
    root = tmp_path / "runtime"
    root.mkdir()
    reservation = _terminal_reservation()
    journal = create_transaction_journal(reservation, created_at=active.created_at)
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=active.created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    bootstrap.publish_active_envelope_if_absent(envelope)
    store = ControlLeaseStore(policy)
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


def test根替换结果仅将明确根漂移视为预期闭锁() -> None:
    """非根错误不得在根替换 worker 中被伪装成预期闭锁。"""
    classifier = getattr(runtime_store_race_support, "_is_expected_root_drift", None)

    assert callable(classifier)
    assert not classifier(ControlLeaseStoreError("control lease transition 参数无效"))
    assert classifier(
        ControlLeaseStoreError("运行时根目录身份或可见路径已漂移"),
    )
    try:
        try:
            raise RuntimeRootBindingError("运行时根目录身份已漂移")
        except RuntimeRootBindingError as error:
            raise ControlLeaseStoreError("部署锁或运行时根不可安全访问") from error
    except ControlLeaseStoreError as error:
        assert classifier(error)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("crash_stage", ("after_prepare", "after_freeze", "after_cas"))
def test接管子进程崩溃后只收敛严格pending的唯一赢家(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    """prepare/freeze/CAS 后退出都只能由新 store 完成原 intent，不能猜测新候选。"""
    _store, root, initial, proof = _lease_store_input(tmp_path)
    run_takeover_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=initial,
            crash_stage=crash_stage,
        ),
    )

    assert _pending_path(root).is_file()
    recovery = ControlLeaseStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    with pytest.raises(ControlLeaseStoreError, match="pending|未收敛"):
        recovery.load_current()

    winner = recovery.recover_pending_transition()

    assert winner is not None
    assert winner.record.predecessor_sha256 == initial.sha256
    assert recovery.load_current() == winner
    assert not _pending_path(root).exists()
    assert _history_digests(root, initial) == {initial.sha256, winner.sha256}
    with pytest.raises(ControlLeaseStoreError, match="proof|CAS|当前"):
        with recovery.mutation(proof):
            pass
    assert _history_digests(root, initial) == {initial.sha256, winner.sha256}


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("crash_stage", ("after_prepare", "after_freeze", "after_cas"))
def test退休子进程崩溃后只能完成原tombstone(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    """退休决定一旦 pending，恢复不得重新请求 proof 或创造第三份 history。"""
    _store, root, initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    run_control_lease_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=initial,
            crash_stage=crash_stage,
            operation="retire",
            completed=completed,
            evidence=evidence,
        ),
    )

    recovery = ControlLeaseStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    with pytest.raises(ControlLeaseStoreError, match="pending|未收敛"):
        recovery.load_current()
    tombstone = recovery.recover_pending_transition()

    assert tombstone is not None
    assert tombstone.record.status.value == "retired"
    assert tombstone.record.retired_from_sha256 == initial.sha256
    assert recovery.load_current() == tombstone
    assert not _pending_path(root).exists()
    assert _history_digests(root, initial) == {initial.sha256, tombstone.sha256}
    with pytest.raises(ControlLeaseStoreError, match="proof|当前|活动"):
        with recovery.mutation(proof):
            pass


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("crash_stage", ("after_prepare", "after_freeze", "after_cas"))
def test不同attempt退休前驱后的初始签发可在崩溃后收敛(
    tmp_path: Path,
    crash_stage: str,
) -> None:
    """initial 可精确使用不同 attempt tombstone，而不从目录扫描推导候选。"""
    store, root, initial, proof, completed, evidence = _terminal_store_input(tmp_path)
    previous = store.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-21T00:00:03Z",
    )
    reservation = replace(
        _reservation(),
        attempt_id="f" * 32,
        plan_sha256="f" * 64,
    )
    journal = create_transaction_journal(reservation, created_at=reservation.created_at)
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=reservation.created_at,
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    bootstrap = RuntimeTransactionBootstrapStore(policy)
    bootstrap.reserve_attempt(reservation)
    bootstrap.write_journal_genesis_once(journal)
    bootstrap.write_envelope_once(envelope)
    write_managed_bytes_atomic(
        active_recovery_envelope_path(root),
        encode_recovery_envelope(envelope),
        root=root,
        policy=_managed_policy(),
    )
    run_control_lease_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=previous,
            crash_stage=crash_stage,
            operation="initial",
        ),
    )

    recovery = ControlLeaseStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    winner = recovery.recover_pending_transition()

    assert winner is not None
    assert winner.record.attempt_id == reservation.attempt_id
    assert winner.record.epoch == 1
    assert winner.record.predecessor_sha256 is None
    assert recovery.load_current() == winner
    assert not _pending_path(root).exists()
    assert _history_digests(root, winner) == {winner.sha256}


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test篡改pending后任何读取或恢复都闭锁且保留证据(tmp_path: Path) -> None:
    """无法解码的 pending 不得被忽略、覆盖或删除。"""
    _store, root, initial, _proof = _lease_store_input(tmp_path)
    run_control_lease_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=initial,
            crash_stage="after_freeze",
        ),
    )
    pending = _pending_path(root)
    write_managed_bytes_atomic(
        pending,
        b'{"schema_version":999}',
        root=root,
        policy=_managed_policy(),
    )
    recovery = ControlLeaseStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )

    with pytest.raises(ControlLeaseStoreError):
        recovery.load_current()
    with pytest.raises(ControlLeaseStoreError):
        recovery.recover_pending_transition()

    assert pending.read_bytes() == b'{"schema_version":999}'


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testcurrent偏离pending的expected和next时恢复拒绝猜测(tmp_path: Path) -> None:
    """current 漂移时不能扫描孤儿 history 或删除原 pending。"""
    _store, root, initial, _proof = _lease_store_input(tmp_path)
    run_control_lease_transition_crash(
        ControlLeaseTransitionCrashInput(
            root=str(root),
            owner_uid=os.geteuid(),
            current=initial,
            crash_stage="after_freeze",
        ),
    )
    alternate, _ = recover_control_lease(
        initial.record,
        _reservation(),
        epoch=initial.record.epoch + 1,
        token=bytes(range(64, 96)),
        owner="current-drift",
        issued_at="2026-07-21T00:00:03Z",
    )
    policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(policy)
    with policy.root_binding.bind() as bound_root:
        with deployment_lock_at(bound_root):
            persistence.freeze_history(bound_root, alternate)
            persistence.compare_and_swap_current(bound_root, initial, alternate)

    with pytest.raises(ControlLeaseStoreError, match="不一致|拒绝猜测"):
        ControlLeaseStore(policy).recover_pending_transition()
    assert _pending_path(root).is_file()
    with policy.root_binding.bind() as bound_root:
        assert persistence.load_current_required(bound_root).record == alternate


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpending持久层拒绝来自不同policy的根租约(tmp_path: Path) -> None:
    """pending 与 current/history 一样不能跨 RuntimeRootBinding 使用。"""
    root = tmp_path / "runtime"
    root.mkdir()
    first = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    second = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    persistence = ControlLeasePersistence(second)

    with first.root_binding.bind() as foreign_root:
        with pytest.raises(ControlLeasePersistenceError, match="其他绑定|已失效"):
            persistence.load_pending_transition_or_none(foreign_root)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test无锁读取在pending后置检查发现并拒绝旧current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读取期间才出现 pending 也不能返回已读取的旧授权状态。"""
    store, root, initial, _proof = _lease_store_input(tmp_path)
    intent = _takeover_intent(initial)
    original = ControlLeasePersistence.load_current_or_none
    installed = False

    def install_pending_after_read(self, bound_root):
        nonlocal installed
        current = original(self, bound_root)
        if not installed:
            installed = True
            self.prepare_pending_transition(bound_root, intent)
        return current

    monkeypatch.setattr(
        ControlLeasePersistence,
        "load_current_or_none",
        install_pending_after_read,
    )
    with pytest.raises(ControlLeaseStoreError, match="pending|未收敛"):
        store.load_current()
    with pytest.raises(ControlLeaseStoreError, match="pending|未收敛"):
        store.load_active_lineage(initial.record)
    assert _pending_path(root).is_file()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpending冻结后根替换不会向新的可见根写入(tmp_path: Path) -> None:
    """旧绑定在 history 后闭锁，替换命名根不能获得 current/pending/history。"""
    _store, root, initial, _proof = _lease_store_input(tmp_path)
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    result = run_takeover_transition_root_swap(
        ControlLeaseTransitionRootSwapInput(
            root=str(root),
            replacement=str(replacement),
            owner_uid=os.geteuid(),
            current=initial,
        ),
    )

    displaced = root.with_name("runtime-displaced")
    assert result.outcome == "expected_root_drift"
    assert not _pending_path(root).exists()
    assert not control_lease_record_path(root).exists()
    assert not control_lease_history_path(
        root,
        initial.record.attempt_id,
        initial.sha256,
    ).parent.exists()
    assert _pending_path(displaced).is_file()
