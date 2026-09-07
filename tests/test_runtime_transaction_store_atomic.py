"""预租约 bootstrap 首写的锁边界与根身份原子性测试。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform._runtime_managed_file_bound as bound_file
import codev_platform.runtime_transaction_store as transaction_store_module
from codev_platform.runtime_attempt_contract import (
    AttemptOperation,
    AttemptReservation,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    create_recovery_envelope,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
    deployment_lock_at,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    create_transaction_journal,
)
from codev_platform.runtime_transaction_store import (
    RuntimeTransactionBootstrapStore,
    RuntimeTransactionStoreError,
)


_POSIX = os.name == "posix"


def _input(
    root: Path,
) -> tuple[
    RuntimeTransactionBootstrapStore,
    AttemptReservation,
    TransactionJournal,
    RecoveryEnvelope,
]:
    reservation = AttemptReservation(
        schema_version=1,
        attempt_id="a" * 32,
        operation=AttemptOperation.DEPLOY,
        plan_sha256="b" * 64,
        controller_sha256="c" * 64,
        created_at="2026-07-21T00:00:00Z",
    )
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


def _write_prefix(
    store: RuntimeTransactionBootstrapStore,
    reservation: AttemptReservation,
    journal: TransactionJournal,
) -> None:
    store.reserve_attempt(reservation)
    store.write_journal_genesis_once(journal)


def _replace_root(root: Path, replacement: Path) -> Path:
    previous = root.with_name(f"{root.name}-previous")
    root.rename(previous)
    replacement.rename(root)
    return previous


def _copy_replacement(root: Path, tmp_path: Path) -> Path:
    replacement = tmp_path / "replacement"
    shutil.copytree(root, replacement)
    return replacement


def _swap_after_final_exclusive_check(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    replacement: Path,
) -> list[Path]:
    original_publish = bound_file._rename_noreplace_at
    previous: list[Path] = []

    def publish_after_swap(
        source: str,
        destination: str,
        source_parent: int,
        destination_parent: int,
    ) -> None:
        previous.append(_replace_root(root, replacement))
        original_publish(source, destination, source_parent, destination_parent)

    monkeypatch.setattr(bound_file, "_rename_noreplace_at", publish_after_swap)
    return previous


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test预留通过同一根租约调用at独占创建(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, _journal, _envelope = _input(root)
    original_create = transaction_store_module.create_managed_bytes_exclusive_at
    observed_roots: list[BoundRuntimeRoot] = []

    def observe_create(
        path: Path,
        payload: bytes,
        *,
        root: BoundRuntimeRoot,
        policy: object,
    ) -> object:
        observed_roots.append(root)
        return original_create(path, payload, root=root, policy=policy)

    monkeypatch.setattr(
        transaction_store_module,
        "create_managed_bytes_exclusive_at",
        observe_create,
    )

    assert store.reserve_attempt(reservation).value == reservation
    assert len(observed_roots) == 1
    assert isinstance(observed_roots[0], BoundRuntimeRoot)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test写入attempt_envelope只创建一次根租约(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _input(root)
    _write_prefix(store, reservation, journal)
    policy = store._policy
    original_bind = type(policy.root_binding).bind
    bind_calls = 0

    @contextmanager
    def count_bind(binding: object) -> Iterator[object]:
        nonlocal bind_calls
        bind_calls += 1
        with original_bind(binding) as bound_root:
            yield bound_root

    monkeypatch.setattr(type(policy.root_binding), "bind", count_bind)

    assert store.write_envelope_once(envelope).value == envelope
    assert bind_calls == 1


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test首次attempt_envelope创建必须位于绑定部署锁内(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _input(root)
    _write_prefix(store, reservation, journal)
    original_create = transaction_store_module.create_managed_bytes_exclusive_at
    original_lock = deployment_lock_at
    locked = False

    @contextmanager
    def tracked_deployment_lock(bound_root: BoundRuntimeRoot) -> Iterator[None]:
        nonlocal locked
        assert not locked
        locked = True
        try:
            with original_lock(bound_root):
                yield
        finally:
            locked = False

    def require_lock(path: Path, *args: object, **kwargs: object) -> object:
        if path == attempt_recovery_envelope_path(root, reservation.attempt_id):
            assert locked, "首次 attempt envelope 不能在部署锁外创建"
        return original_create(path, *args, **kwargs)

    monkeypatch.setattr(
        transaction_store_module,
        "deployment_lock_at",
        tracked_deployment_lock,
    )
    monkeypatch.setattr(
        transaction_store_module,
        "create_managed_bytes_exclusive_at",
        require_lock,
    )

    assert store.write_envelope_once(envelope).value == envelope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test首次active发布必须位于绑定部署锁内(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _input(root)
    _write_prefix(store, reservation, journal)
    store.write_envelope_once(envelope)
    original_create = transaction_store_module.create_managed_bytes_exclusive_at
    original_lock = deployment_lock_at
    locked = False

    @contextmanager
    def tracked_deployment_lock(bound_root: BoundRuntimeRoot) -> Iterator[None]:
        nonlocal locked
        assert not locked
        locked = True
        try:
            with original_lock(bound_root):
                yield
        finally:
            locked = False

    def require_lock(path: Path, *args: object, **kwargs: object) -> object:
        if path == active_recovery_envelope_path(root):
            assert locked, "首次活动 envelope 不能在部署锁外创建"
        return original_create(path, *args, **kwargs)

    monkeypatch.setattr(
        transaction_store_module,
        "deployment_lock_at",
        tracked_deployment_lock,
    )
    monkeypatch.setattr(
        transaction_store_module,
        "create_managed_bytes_exclusive_at",
        require_lock,
    )

    assert store.publish_active_envelope_if_absent(envelope).value == envelope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "operation",
    ("预留", "journal", "attempt_envelope", "active_envelope"),
)
def test四类bootstrap首写在最终发布检查后换根不写替换命名根(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _input(root)
    if operation == "journal":
        store.reserve_attempt(reservation)
    if operation in {"attempt_envelope", "active_envelope"}:
        _write_prefix(store, reservation, journal)
    if operation == "active_envelope":
        store.write_envelope_once(envelope)
    replacement = _copy_replacement(root, tmp_path)
    previous = _swap_after_final_exclusive_check(monkeypatch, root, replacement)
    actions = {
        "预留": lambda: store.reserve_attempt(reservation),
        "journal": lambda: store.write_journal_genesis_once(journal),
        "attempt_envelope": lambda: store.write_envelope_once(envelope),
        "active_envelope": lambda: store.publish_active_envelope_if_absent(envelope),
    }
    expected_paths = {
        "预留": attempt_reservation_path(root, reservation.attempt_id),
        "journal": attempt_journal_path(root, reservation.attempt_id),
        "attempt_envelope": attempt_recovery_envelope_path(root, reservation.attempt_id),
        "active_envelope": active_recovery_envelope_path(root),
    }

    with pytest.raises(RuntimeTransactionStoreError, match="根|运行时"):
        actions[operation]()

    assert previous
    assert not expected_paths[operation].exists()
    with pytest.raises(RuntimeTransactionStoreError, match="根|运行时"):
        actions[operation]()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test非规范活动叶子不得退化为expected_absent冲突(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    store, reservation, journal, envelope = _input(root)
    _write_prefix(store, reservation, journal)
    store.write_envelope_once(envelope)
    active_recovery_envelope_path(root).write_bytes(b"{")
    os.chmod(active_recovery_envelope_path(root), 0o600)

    with pytest.raises(RuntimeTransactionStoreError, match="活动恢复 envelope"):
        store.publish_active_envelope_if_absent(envelope)
