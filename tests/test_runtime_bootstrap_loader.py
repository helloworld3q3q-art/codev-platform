"""严格持久 bootstrap loader 的真值边界测试。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
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
    RecoveryEnvelope,
    create_recovery_envelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_root_binding import RuntimeRootBindingError
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
    control_lease_record_path,
    deployment_lock_at,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_codec import encode_transaction_journal
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
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore


_POSIX = os.name == "posix"


def _policy(root: Path, *, owner_uid: int | None = None) -> RuntimeStorePolicy:
    return RuntimeStorePolicy(
        root=root,
        owner_uid=os.geteuid() if owner_uid is None else owner_uid,
    )


def _reservation() -> AttemptReservation:
    return AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )


def _bootstrap_input(
    root: Path,
) -> tuple[
    RuntimeTransactionBootstrapStore,
    AttemptReservation,
    TransactionJournal,
    RecoveryEnvelope,
]:
    reservation = _reservation()
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
    return RuntimeTransactionBootstrapStore(_policy(root)), reservation, journal, envelope


def _publish_bootstrap(
    store: RuntimeTransactionBootstrapStore,
    reservation: AttemptReservation,
    journal: TransactionJournal,
    envelope: RecoveryEnvelope,
) -> None:
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)
    store.write_envelope_once(envelope)
    store.publish_active_envelope_if_absent(envelope)


def _managed_policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(
        mode=0o600,
        require_uid=os.geteuid(),
        max_bytes=MAX_TRANSACTION_JOURNAL_BYTES,
    )


def _write_journal_head(root: Path, journal: TransactionJournal) -> None:
    write_managed_bytes_atomic(
        attempt_journal_path(root, journal.attempt_id),
        encode_transaction_journal(journal),
        root=root,
        policy=_managed_policy(),
    )


def _replace_root_with_copy(root: Path, replacement: Path) -> Path:
    """以另一份合法目录替换固定根路径，保留旧根供写入断言。"""
    previous = root.with_name(f"{root.name}-previous")
    root.rename(previous)
    replacement.rename(root)
    return previous


def _advanced_journal(journal: TransactionJournal) -> TransactionJournal:
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
        recorded_at="2026-07-21T00:00:01Z",
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
        updated_at=action.recorded_at,
        completed_at=None,
    )


def _completed_journal(journal: TransactionJournal) -> TransactionJournal:
    actions = tuple(
        TransactionAction(
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
            state=state,
            control_lease_epoch_audit=1,
            control_token_sha256_audit="1" * 64,
            control_lease_record_sha256_audit="2" * 64,
            recorded_at=recorded_at,
        )
        for state, recorded_at in (
            (TransactionActionState.PREPARED, "2026-07-21T00:00:01Z"),
            (TransactionActionState.APPLIED, "2026-07-21T00:00:02Z"),
            (TransactionActionState.COMMITTED, "2026-07-21T00:00:03Z"),
        )
    )
    return TransactionJournal(
        schema_version=journal.schema_version,
        attempt_id=journal.attempt_id,
        reservation_sha256=journal.reservation_sha256,
        journal_genesis_sha256=journal.journal_genesis_sha256,
        status=TransactionJournalStatus.COMPLETED,
        actions=actions,
        terminal_evidence_sha256="e" * 64,
        created_at=journal.created_at,
        updated_at="2026-07-21T00:00:04Z",
        completed_at="2026-07-21T00:00:04Z",
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test严格loader只从持久活动三元组构造上下文(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)

    loader = PersistedActiveBootstrapLoader(_policy(root))
    context = loader.load_active_context()

    assert loader.root == root
    assert loader.load_active_envelope() == context.active_envelope
    assert context.reservation.value == reservation
    assert context.journal_genesis.value == journal
    assert context.active_envelope.value == envelope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test运行时根绑定拒绝两份合法目录之间的替换(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = _policy(root)
    policy.root_binding.verify()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    _replace_root_with_copy(root, replacement)

    with pytest.raises(ValueError, match="运行时根"):
        policy.root_binding.verify()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test严格loader拒绝首次读取后被合法替换的根(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    replacement = tmp_path / "replacement"
    shutil.copytree(root, replacement)
    loader = PersistedActiveBootstrapLoader(_policy(root))
    original = loader._load_reservation

    def replace_then_read(attempt_id: str, bound_root: object) -> object:
        _replace_root_with_copy(root, replacement)
        return original(attempt_id, bound_root)

    monkeypatch.setattr(loader, "_load_reservation", replace_then_read)

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="运行时根"):
        loader.load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "build_head",
    (_advanced_journal, _completed_journal),
    ids=("推进活动head", "完成head"),
)
def test严格loader从推进或完成head派生稳定genesis(
    tmp_path: Path,
    build_head: Callable[[TransactionJournal], TransactionJournal],
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, _reservation_value, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, _reservation(), journal, envelope)
    _write_journal_head(root, build_head(journal))

    context = PersistedActiveBootstrapLoader(_policy(root)).load_active_context()

    assert context.journal_genesis.value == journal
    assert context.active_envelope.value == envelope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "path_selector",
    (
        active_recovery_envelope_path,
        lambda root: attempt_recovery_envelope_path(root, "a" * 32),
        lambda root: attempt_reservation_path(root, "a" * 32),
        lambda root: attempt_journal_path(root, "a" * 32),
    ),
    ids=("活动envelope", "attempt_envelope", "reservation", "journal_head"),
)
def test严格loader拒绝任一持久bootstrap叶子缺失(
    tmp_path: Path,
    path_selector: Callable[[Path], Path],
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    path_selector(root).unlink()

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="安全加载|严格|活动"):
        PersistedActiveBootstrapLoader(_policy(root)).load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("payload_kind", ("截断", "未知字段"))
def test严格loader拒绝活动envelope非严格载荷(
    tmp_path: Path,
    payload_kind: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    canonical = encode_recovery_envelope(envelope)
    payload = b"{" if payload_kind == "截断" else canonical[:-1] + b',"unknown":1}'
    write_managed_bytes_atomic(
        active_recovery_envelope_path(root),
        payload,
        root=root,
        policy=_managed_policy(),
    )

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="活动恢复 envelope"):
        PersistedActiveBootstrapLoader(_policy(root)).load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test严格loader拒绝活动与attempt_envelope字节漂移(tmp_path: Path) -> None:
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
    write_managed_bytes_atomic(
        active_recovery_envelope_path(root),
        encode_recovery_envelope(drifted),
        root=root,
        policy=_managed_policy(),
    )

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="活动|漂移|一致"):
        PersistedActiveBootstrapLoader(_policy(root)).load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test严格loader拒绝不匹配属主并且外层部署锁不重入(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)

    policy = _policy(root)
    with policy.root_binding.bind() as bound_root:
        with deployment_lock_at(bound_root):
            context = PersistedActiveBootstrapLoader(policy).load_active_context(
                bound_root=bound_root,
            )
    assert context.active_envelope.value == envelope

    with pytest.raises(
        PersistedActiveBootstrapLoaderError,
        match="运行时根租约不可安全使用",
    ) as caught:
        PersistedActiveBootstrapLoader(
            _policy(root, owner_uid=os.geteuid() + 1),
        ).load_active_context()
    assert isinstance(caught.value.__cause__, RuntimeRootBindingError)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test严格loader在根被替换后闭锁且不触碰外部目录(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(store, reservation, journal, envelope)
    loader = PersistedActiveBootstrapLoader(_policy(root))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside")
    moved = tmp_path / "runtime-old"
    root.rename(moved)
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="运行时根"):
        loader.load_active_context()

    assert sentinel.read_bytes() == b"outside"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testcontrollease只能从loader读取的真实磁盘bootstrap签发(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    policy = _policy(root)
    store = ControlLeaseStore(policy)

    with pytest.raises(ControlLeaseStoreError, match="bootstrap|活动|加载"):
        store.acquire_initial(
            owner="controller-a",
            token=bytes(range(32)),
            issued_at="2026-07-21T00:00:01Z",
        )

    assert not control_lease_record_path(root).exists()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    issued, _proof = store.acquire_initial(
        owner="controller-a",
        token=bytes(range(32)),
        issued_at="2026-07-21T00:00:01Z",
    )

    assert issued.record.attempt_id == reservation.attempt_id


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testcontrollease在loader返回后根替换时不写任一根(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    policy = _policy(root)
    store = ControlLeaseStore(policy)
    replacement = tmp_path / "replacement"
    shutil.copytree(root, replacement)
    previous: Path | None = None
    original = store._transitions.load_active_context

    def replace_after_context(bound_root: object) -> object:
        nonlocal previous
        context = original(bound_root)
        previous = _replace_root_with_copy(root, replacement)
        return context

    monkeypatch.setattr(
        store._transitions,
        "load_active_context",
        replace_after_context,
    )

    with pytest.raises(ControlLeaseStoreError, match="运行时根|bootstrap"):
        store.acquire_initial(
            owner="controller-a",
            token=bytes(range(32)),
            issued_at="2026-07-21T00:00:01Z",
        )

    assert previous is not None
    assert not control_lease_record_path(root).exists()
    assert not control_lease_record_path(previous).exists()
    assert not tuple((root / "control-leases").rglob("*.json"))
    assert not tuple((previous / "control-leases").rglob("*.json"))
