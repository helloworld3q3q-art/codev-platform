"""预租约事务 bootstrap store 测试。"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

import codev_platform.runtime_transaction_store as transaction_store_module
from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
    encode_attempt_reservation,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    write_managed_bytes_atomic,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    create_recovery_envelope,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_contract import (
    CURRENT_SERVE_PERMIT_RESOURCE_ID,
    MAX_TRANSACTION_JOURNAL_BYTES,
    SERVE_PERMIT_RESOURCE_KIND,
    TransactionAction,
    TransactionActionIntent,
    TransactionActionState,
    TransactionJournal,
    TransactionJournalStatus,
    create_transaction_journal,
)
from codev_platform.runtime_transaction_codec import encode_transaction_journal
from codev_platform.runtime_storage import (
    RuntimeStoragePathError,
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
)
from codev_platform.runtime_transaction_store import (
    ActiveEnvelopeConflictError,
    AttemptReservationConflictError,
    BootstrapImmutableConflictError,
    RuntimeTransactionBootstrapStore,
    RuntimeTransactionStoreError,
)


_POSIX = os.name == "posix"


def _reservation(
    attempt_id: str,
    *,
    plan_sha256: str = "b" * 64,
) -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id=attempt_id,
        operation=AttemptOperation.DEPLOY,
        plan_sha256=plan_sha256,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _bootstrap_input(
    root: Path,
    *,
    attempt_id: str = "a" * 32,
) -> tuple[
    RuntimeTransactionBootstrapStore,
    AttemptReservation,
    TransactionJournal,
    RecoveryEnvelope,
]:
    reservation = _reservation(attempt_id)
    journal = create_transaction_journal(
        reservation,
        created_at=reservation.created_at,
    )
    envelope = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=reservation.created_at,
    )
    store = RuntimeTransactionBootstrapStore(
        RuntimeStorePolicy(root=root, owner_uid=os.geteuid()),
    )
    return store, reservation, journal, envelope


def _publish_bootstrap(
    store: RuntimeTransactionBootstrapStore,
    reservation: AttemptReservation,
    journal: TransactionJournal,
    envelope: RecoveryEnvelope,
) -> None:
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    store.write_envelope_once(envelope)


def _advanced_journal(
    journal: TransactionJournal,
    *,
    recorded_at: str = "2026-07-21T00:00:01Z",
) -> TransactionJournal:
    action = TransactionAction(
        schema_version=1,
        attempt_id=journal.attempt_id,
        reservation_sha256=journal.reservation_sha256,
        step_sequence=1,
        resource_kind=SERVE_PERMIT_RESOURCE_KIND,
        resource_id=CURRENT_SERVE_PERMIT_RESOURCE_ID,
        intent=TransactionActionIntent.REVOKE_CURRENT_SERVE_PERMIT,
        operation_sha256="e" * 64,
        before_sha256="f" * 64,
        after_sha256="a" * 64,
        state=TransactionActionState.PREPARED,
        control_lease_epoch_audit=1,
        control_token_sha256_audit="1" * 64,
        control_lease_record_sha256_audit="2" * 64,
        recorded_at=recorded_at,
    )
    return TransactionJournal(
        schema_version=journal.schema_version,
        attempt_id=journal.attempt_id,
        reservation_sha256=journal.reservation_sha256,
        journal_genesis_sha256=journal.journal_genesis_sha256,
        status=TransactionJournalStatus.ACTIVE,
        actions=(action,),
        terminal_evidence_sha256=None,
        created_at=journal.created_at,
        updated_at=recorded_at,
        completed_at=None,
    )


def _write_journal_head(root: Path, journal: TransactionJournal) -> None:
    write_managed_bytes_atomic(
        attempt_journal_path(root, journal.attempt_id),
        encode_transaction_journal(journal),
        root=root,
        policy=ManagedFilePolicy(
            mode=0o600,
            require_uid=os.geteuid(),
            max_bytes=MAX_TRANSACTION_JOURNAL_BYTES,
        ),
    )


def _head_with_drifted_genesis(reservation: AttemptReservation) -> TransactionJournal:
    return _advanced_journal(
        create_transaction_journal(
            reservation,
            created_at="2026-07-21T00:00:01Z",
        ),
        recorded_at="2026-07-21T00:00:02Z",
    )


def _head_with_drifted_reservation(
    reservation: AttemptReservation,
) -> TransactionJournal:
    foreign = _reservation(reservation.attempt_id, plan_sha256="e" * 64)
    return _advanced_journal(
        create_transaction_journal(foreign, created_at=foreign.created_at),
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test预租约链只能按reservation_journal_envelope_active_envelope发布(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)

    _publish_bootstrap(store, reservation, journal, envelope)
    active = store.publish_active_envelope_if_absent(envelope)

    assert active.value == envelope
    assert store.load_active_envelope() == active
    assert not (root / "control-lease.json").exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动envelope已存在时拒绝第二次发布且不覆盖(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    first_store, first_reservation, first_journal, first_envelope = _bootstrap_input(root)
    second_store, second_reservation, second_journal, second_envelope = _bootstrap_input(
        root,
        attempt_id="e" * 32,
    )
    _publish_bootstrap(
        first_store,
        first_reservation,
        first_journal,
        first_envelope,
    )
    _publish_bootstrap(
        second_store,
        second_reservation,
        second_journal,
        second_envelope,
    )
    first_active = first_store.publish_active_envelope_if_absent(first_envelope)

    with pytest.raises(ActiveEnvelopeConflictError, match="已存在"):
        second_store.publish_active_envelope_if_absent(second_envelope)

    assert first_store.load_active_envelope() == first_active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test加载reservation拒绝路径attempt_id与载荷身份漂移(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, _journal, _envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    forged = _reservation("e" * 32)
    path = attempt_reservation_path(root, reservation.attempt_id)
    path.write_bytes(encode_attempt_reservation(forged))
    os.chmod(path, 0o600)

    with pytest.raises(RuntimeTransactionStoreError, match="路径身份"):
        store.load_reservation(reservation.attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testreservation重复预留始终冲突(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, _journal, _envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)

    with pytest.raises(AttemptReservationConflictError, match="已存在"):
        store.reserve_attempt(reservation)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testjournal_genesis仅相同规范字节可以幂等(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, _envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    first = store.write_journal_genesis_once(journal)
    drifted = create_transaction_journal(
        reservation,
        created_at="2026-07-21T00:00:01Z",
    )

    assert store.write_journal_genesis_once(journal) == first
    with pytest.raises(BootstrapImmutableConflictError, match="内容漂移"):
        store.write_journal_genesis_once(drifted)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testenvelope仅相同规范字节可以幂等(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    first = store.write_envelope_once(envelope)
    drifted = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="other-bootstrap",
        created_at=reservation.created_at,
    )

    assert store.write_envelope_once(envelope) == first
    with pytest.raises(BootstrapImmutableConflictError, match="内容漂移"):
        store.write_envelope_once(drifted)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testenvelope拒绝与已持久化journal_genesis漂移(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, _envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    drifted_journal = create_transaction_journal(
        reservation,
        created_at="2026-07-21T00:00:01Z",
    )
    drifted = create_recovery_envelope(
        reservation,
        drifted_journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-bootstrap",
        created_at=reservation.created_at,
    )

    with pytest.raises(RuntimeTransactionStoreError, match="不精确绑定"):
        store.write_envelope_once(drifted)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testbootstrap链拒绝跳过前序记录(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)

    with pytest.raises(RuntimeTransactionStoreError, match="reservation"):
        store.write_journal_genesis_once(journal)
    store.reserve_attempt(reservation)
    with pytest.raises(RuntimeTransactionStoreError, match="journal head 无法安全加载"):
        store.write_envelope_once(envelope)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动envelope必须与attempt副本完全相同(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    drifted = create_recovery_envelope(
        reservation,
        journal,
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="other-bootstrap",
        created_at=reservation.created_at,
    )

    with pytest.raises(BootstrapImmutableConflictError, match="内容漂移"):
        store.publish_active_envelope_if_absent(drifted)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test截断活动envelope会在读取时闭锁失败(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    store.publish_active_envelope_if_absent(envelope)
    path = active_recovery_envelope_path(root)
    path.write_bytes(b"{")
    os.chmod(path, 0o600)

    with pytest.raises(RuntimeTransactionStoreError, match="严格解码"):
        store.load_active_envelope()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testreservation符号链接仍然冲突且不跟随(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, _journal, _envelope = _bootstrap_input(root)
    target = root / "attempts" / reservation.attempt_id
    target.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    (target / "reservation.json").symlink_to(outside)

    with pytest.raises(AttemptReservationConflictError, match="已存在"):
        store.reserve_attempt(reservation)

    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test读取活动envelope不获取部署锁(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    active = store.publish_active_envelope_if_absent(envelope)

    def reject_lock(*args: object, **kwargs: object) -> object:
        raise AssertionError("只读活动 envelope 不得获取部署锁")

    monkeypatch.setattr(transaction_store_module, "deployment_lock_at", reject_lock)

    assert store.load_active_envelope() == active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动envelope读取允许合法推进的journal_head(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    active = store.publish_active_envelope_if_absent(envelope)
    _write_journal_head(root, _advanced_journal(journal))

    assert store.load_active_envelope() == active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test首次写入envelope拒绝已经推进的journal_head(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    _write_journal_head(root, _advanced_journal(journal))

    with pytest.raises(
        RuntimeTransactionStoreError,
        match="当前 journal head 不是精确空 genesis",
    ):
        store.write_envelope_once(envelope)

    assert not attempt_recovery_envelope_path(root, reservation.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test首次发布活动envelope拒绝已经推进的journal_head(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    _write_journal_head(root, _advanced_journal(journal))

    with pytest.raises(
        RuntimeTransactionStoreError,
        match="当前 journal head 不是精确空 genesis",
    ):
        store.publish_active_envelope_if_absent(envelope)

    assert not active_recovery_envelope_path(root).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testjournal推进后bootstrap不可变记录仍按相同字节幂等(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    store.reserve_attempt(reservation)
    persisted_journal = store.write_journal_genesis_once(journal)
    persisted_envelope = store.write_envelope_once(envelope)
    _write_journal_head(root, _advanced_journal(journal))

    assert store.write_journal_genesis_once(journal) == persisted_journal
    assert store.write_envelope_once(envelope) == persisted_envelope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "build_head",
    (_head_with_drifted_genesis, _head_with_drifted_reservation),
    ids=("genesis漂移", "reservation漂移"),
)
def test活动envelope读取拒绝稳定journal绑定漂移(
    tmp_path: Path,
    build_head: Callable[[AttemptReservation], TransactionJournal],
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    store.publish_active_envelope_if_absent(envelope)
    _write_journal_head(root, build_head(reservation))

    with pytest.raises(RuntimeTransactionStoreError, match="摘要|不精确绑定"):
        store.load_active_envelope()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "entry",
    (
        "预留",
        "读取预留",
        "写入journal",
        "写入envelope",
        "发布active",
        "读取active",
    ),
)
def test根在构造后替换为符号链接时公开入口闭锁失败(
    tmp_path: Path,
    entry: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"outside")
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)
    entries = {
        "预留": lambda: store.reserve_attempt(reservation),
        "读取预留": lambda: store.load_reservation(reservation.attempt_id),
        "写入journal": lambda: store.write_journal_genesis_once(journal),
        "写入envelope": lambda: store.write_envelope_once(envelope),
        "发布active": lambda: store.publish_active_envelope_if_absent(envelope),
        "读取active": store.load_active_envelope,
    }

    with pytest.raises(RuntimeTransactionStoreError, match="运行时根"):
        entries[entry]()

    assert sentinel.read_bytes() == b"outside"
    assert not (outside / "attempts").exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test根仍可信时保留无效attempt_id的领域错误(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, _reservation, _journal, _envelope = _bootstrap_input(root)

    with pytest.raises(RuntimeStoragePathError, match="attempt_id"):
        store.load_reservation("invalid")


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动bootstrap上下文只返回已持久化的空journal_genesis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    active = store.publish_active_envelope_if_absent(envelope)

    context = store.load_active_bootstrap_context()

    assert context.reservation.value == reservation
    assert context.journal_genesis.value == journal
    assert context.active_envelope == active
    assert store.root == root


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活动bootstrap上下文允许合法推进并派生空journal_genesis(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    active = store.publish_active_envelope_if_absent(envelope)
    _write_journal_head(root, _advanced_journal(journal))

    context = store.load_active_bootstrap_context()

    assert context.reservation.value == reservation
    assert context.journal_genesis.value == journal
    assert context.active_envelope == active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "build_head",
    (_head_with_drifted_genesis, _head_with_drifted_reservation),
    ids=("genesis漂移", "reservation漂移"),
)
def test活动bootstrap上下文拒绝稳定journal身份漂移(
    tmp_path: Path,
    build_head: Callable[[AttemptReservation], TransactionJournal],
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    store.publish_active_envelope_if_absent(envelope)
    _write_journal_head(root, build_head(reservation))

    with pytest.raises(RuntimeTransactionStoreError, match="journal genesis|摘要|不精确绑定"):
        store.load_active_bootstrap_context()
