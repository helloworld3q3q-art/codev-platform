"""严格 bootstrap loader 的根租约与失败闭锁回归。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform.runtime_bootstrap_loader as loader_module
from codev_platform.runtime_attempt_contract import AttemptOperation, AttemptReservation
from codev_platform.runtime_bootstrap_loader import (
    PersistedActiveBootstrapLoader,
    PersistedActiveBootstrapLoaderError,
)
from codev_platform.runtime_recovery_contract import (
    RecoveryEnvelope,
    create_recovery_envelope,
    encode_recovery_envelope,
)
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_recovery_envelope_path,
    attempt_reservation_path,
)
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_transaction_contract import (
    TransactionJournal,
    create_transaction_journal,
)
from codev_platform.runtime_transaction_store import RuntimeTransactionBootstrapStore


_POSIX = os.name == "posix"


def _policy(root: Path) -> RuntimeStorePolicy:
    return RuntimeStorePolicy(root=root, owner_uid=os.geteuid())


def _bootstrap_input(
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


def _bootstrap_leaf_path(
    root: Path,
    reservation: AttemptReservation,
    leaf: str,
) -> Path:
    paths = {
        "active": active_recovery_envelope_path(root),
        "reservation": attempt_reservation_path(root, reservation.attempt_id),
        "journal": attempt_journal_path(root, reservation.attempt_id),
        "attempt_envelope": attempt_recovery_envelope_path(
            root,
            reservation.attempt_id,
        ),
    }
    return paths[leaf]


def _damage_bootstrap_leaf(path: Path, damage: str) -> None:
    payload = path.read_bytes()
    if damage == "缺失":
        path.unlink()
        return
    if damage == "截断":
        path.write_bytes(b"{")
        return
    if damage == "未知字段":
        path.write_bytes(payload[:-1] + b',"unknown":1}')
        return
    if damage == "非规范":
        path.write_bytes(payload + b"\n")
        return
    if damage == "错误权限":
        os.chmod(path, 0o640)
        return
    raise AssertionError(f"未知损坏方式：{damage}")


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
def test严格loader外层租约复用同一根且不重新绑定(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    policy = _policy(root)
    loader = PersistedActiveBootstrapLoader(policy)
    original_bind = type(policy.root_binding).bind
    bind_calls = 0
    observed_roots: list[object] = []

    @contextmanager
    def count_bind(binding: object) -> Iterator[object]:
        nonlocal bind_calls
        bind_calls += 1
        with original_bind(binding) as bound_root:
            yield bound_root

    original_read = loader_module.read_managed_bytes_at

    def observe_read(*args: object, **kwargs: object) -> bytes:
        observed_roots.append(kwargs["root"])
        return original_read(*args, **kwargs)

    monkeypatch.setattr(type(policy.root_binding), "bind", count_bind)
    monkeypatch.setattr(loader_module, "read_managed_bytes_at", observe_read)

    with original_bind(policy.root_binding) as bound_root:
        context = loader.load_active_context(bound_root=bound_root)

        assert context.active_envelope.value == envelope
        assert observed_roots
        assert all(root_item is bound_root for root_item in observed_roots)

    assert bind_calls == 0


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
def test严格loader无外层租约只创建一次根绑定(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    policy = _policy(root)
    loader = PersistedActiveBootstrapLoader(policy)
    original_bind = type(policy.root_binding).bind
    bind_calls = 0

    @contextmanager
    def count_bind(binding: object) -> Iterator[object]:
        nonlocal bind_calls
        bind_calls += 1
        with original_bind(binding) as bound_root:
            yield bound_root

    monkeypatch.setattr(type(policy.root_binding), "bind", count_bind)

    assert loader.load_active_context().active_envelope.value == envelope
    assert bind_calls == 1


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
def test严格loader拒绝其他策略签发的根租约(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    expected_policy = _policy(root)
    other_policy = _policy(root)
    loader = PersistedActiveBootstrapLoader(expected_policy)

    with other_policy.root_binding.bind() as foreign_root:
        with pytest.raises(PersistedActiveBootstrapLoaderError, match="根|租约"):
            loader.load_active_context(bound_root=foreign_root)


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
def test既有非规范活动叶子必须报安全错误而非发布冲突(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    path = active_recovery_envelope_path(root)
    path.write_bytes(encode_recovery_envelope(envelope) + b"\n")
    os.chmod(path, 0o600)
    loader = PersistedActiveBootstrapLoader(_policy(root))

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="活动恢复 envelope"):
        loader.require_active_envelope_absent()


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "leaf",
    ("active", "reservation", "journal", "attempt_envelope"),
)
@pytest.mark.parametrize(
    "damage",
    ("缺失", "截断", "未知字段", "非规范", "错误权限"),
)
def test严格loader拒绝任一bootstrap叶子的持久化损坏(
    tmp_path: Path,
    leaf: str,
    damage: str,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    _damage_bootstrap_leaf(_bootstrap_leaf_path(root, reservation, leaf), damage)

    with pytest.raises(PersistedActiveBootstrapLoaderError):
        PersistedActiveBootstrapLoader(_policy(root)).load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
@pytest.mark.parametrize(
    "leaf",
    ("active", "reservation", "journal", "attempt_envelope"),
)
def test严格loader拒绝错误属主的任一bootstrap叶子(
    tmp_path: Path,
    leaf: str,
) -> None:
    if os.geteuid() != 0:
        pytest.skip("当前用户不能构造不同属主的真实受管叶子")
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    path = _bootstrap_leaf_path(root, reservation, leaf)
    os.chown(path, 1, -1)

    with pytest.raises(PersistedActiveBootstrapLoaderError):
        PersistedActiveBootstrapLoader(_policy(root)).load_active_context()


@pytest.mark.skipif(not _POSIX, reason="受管文件仅在 WSL/Linux 验证")
def test严格loader在根目录权限漂移后永久闭锁(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    writer, reservation, journal, envelope = _bootstrap_input(root)
    _publish_bootstrap(writer, reservation, journal, envelope)
    policy = _policy(root)
    loader = PersistedActiveBootstrapLoader(policy)
    policy.root_binding.verify()
    os.chmod(root, 0o777)

    with pytest.raises(PersistedActiveBootstrapLoaderError, match="根"):
        loader.load_active_context()
    with pytest.raises(PersistedActiveBootstrapLoaderError, match="根"):
        loader.load_active_context()
