"""受控 transaction post-lease 存储的负向边界与同根回归。"""

from __future__ import annotations

import dataclasses
import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import codev_platform.runtime_transaction_controlled_store as controlled_store_module
from codev_platform import runtime_transaction_contract as transaction
from codev_platform.runtime_fencing import (
    control_lease_record_sha256,
)
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_journal_path,
    attempt_terminal_evidence_path,
    deployment_lock_at,
)
from codev_platform.runtime_transaction_controlled_store import (
    RuntimeTransactionControlledStoreError,
    RuntimeTransactionStore,
    TerminalEnvelopeCleanupError,
    TransactionRecordConflictError,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeMutationScope,
    RuntimeTerminalCleanupScope,
)
from codev_platform.runtime_transaction_contract import transaction_journal_sha256
from tests.runtime_transaction_controlled_store_support import (
    build_terminal_store_input as _terminal_store_input,
)
from tests.runtime_transaction_terminal_support import _safety_terminal_input


_POSIX = os.name == "posix"


@contextmanager
def _yield_scope(scope: object) -> Iterator[object]:
    """为已关闭 scope 构造只读测试 gate。"""
    yield scope


@contextmanager
def _unexpected_scope(*_args: object, **_kwargs: object) -> Iterator[object]:
    """断言当前用例未意外进入另一种 gate。"""
    raise AssertionError("不应进入此 gate")
    yield None


def _complete_and_retire(tmp_path: Path) -> tuple[object, ...]:
    """构造已完成、已退休且仍保留 active envelope 的清理输入。"""
    store, gate, root, active, completed, evidence, proof = _terminal_store_input(tmp_path)
    store.write_terminal_evidence_once(evidence, proof=proof)
    store.write_journal(
        completed,
        proof=proof,
        expected_sha256=transaction_journal_sha256(active),
    )
    retired = gate.retire_current(
        proof,
        completed,
        evidence,
        retired_at="2026-07-19T10:10:00Z",
    )
    return store, gate, root, active, completed, evidence, proof, retired


def _append_action_for_current_lease(active: object, proof: object) -> object:
    """生成严格绑定当前测试 lease 的合法 append-only journal。"""
    attempt, _journal, record, _expected_proof, _state, _acceptance, _fence = (
        _safety_terminal_input()
    )
    action = transaction.prepare_transaction_action(
        attempt,
        record,
        proof,
        step_sequence=3,
        intent=transaction.TransactionActionIntent.APPLY_RESOURCE,
        resource_kind="generation-state",
        resource_id="hardening",
        operation_sha256="1" * 64,
        before_sha256="2" * 64,
        after_sha256="3" * 64,
        recorded_at="2026-07-19T10:12:00Z",
    )
    return dataclasses.replace(
        active,
        actions=active.actions + (action,),
        updated_at=action.recorded_at,
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpostlease_journal拒绝陈旧expected_sha且不改写head(tmp_path: Path) -> None:
    """CAS 前置摘要不等于当前 head 时，即使记录相同也必须闭锁。"""
    store, _gate, _root, active, _completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )
    stale_sha256 = hashlib.sha256(b"stale-journal-head").hexdigest()
    assert stale_sha256 != transaction_journal_sha256(active)

    with pytest.raises(TransactionRecordConflictError, match="前置摘要"):
        store.write_journal(
            active,
            proof=proof,
            expected_sha256=stale_sha256,
        )

    assert store.load_journal(active.attempt_id) == active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
@pytest.mark.parametrize("leaf", ("journal", "evidence"))
def test单记录读取拒绝截断json并翻译为受控错误(
    tmp_path: Path,
    leaf: str,
) -> None:
    """截断的受控叶子不得穿透为原始 JSON 或受管文件异常。"""
    store, _gate, root, active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    if leaf == "journal":
        path = attempt_journal_path(root, active.attempt_id)
        reader = store.load_journal
        attempt_id = active.attempt_id
    else:
        store.write_terminal_evidence_once(evidence, proof=proof)
        path = attempt_terminal_evidence_path(root, evidence.attempt_id)
        reader = store.load_terminal_evidence
        attempt_id = evidence.attempt_id
    path.write_bytes(b"{")

    with pytest.raises(RuntimeTransactionControlledStoreError):
        reader(attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test单记录读取拒绝终态证据符号链接且不跟随外部文件(
    tmp_path: Path,
) -> None:
    """post-lease 严格读取必须拒绝叶子符号链接。"""
    store, _gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    path = attempt_terminal_evidence_path(root, evidence.attempt_id)
    outside = tmp_path / "outside-evidence.json"
    outside.write_bytes(b"outside")
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(RuntimeTransactionControlledStoreError):
        store.load_terminal_evidence(evidence.attempt_id)

    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test单记录读取拒绝终态证据非普通文件(tmp_path: Path) -> None:
    """目录等非普通叶子不能被当作受控 JSON 读取。"""
    store, _gate, root, _active, _completed, evidence, _proof = _terminal_store_input(
        tmp_path,
    )
    path = attempt_terminal_evidence_path(root, evidence.attempt_id)
    path.mkdir()

    with pytest.raises(RuntimeTransactionControlledStoreError):
        store.load_terminal_evidence(evidence.attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test受控写入拒绝已关闭的mutation_scope且不落盘(tmp_path: Path) -> None:
    """store 必须在任何 loader 或写入前拒绝已经失效的根租约。"""
    store, gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    with gate.mutation(proof) as scope:
        expired_scope = scope

    expired_gate = SimpleNamespace(
        mutation=lambda _proof: _yield_scope(expired_scope),
        terminal_cleanup=_unexpected_scope,
    )
    expired_store = RuntimeTransactionStore(store._policy, expired_gate)

    with pytest.raises(RuntimeTransactionControlledStoreError, match="写入"):
        expired_store.write_terminal_evidence_once(evidence, proof=proof)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test终态清理拒绝已关闭的cleanup_scope且不删除active(tmp_path: Path) -> None:
    """cleanup 也必须在进入临界区后验证同一仍存活的 root 租约。"""
    store, gate, root, _active, _completed, _evidence, _proof, retired = _complete_and_retire(
        tmp_path
    )
    with gate.terminal_cleanup(retired) as scope:
        expired_scope = scope

    expired_gate = SimpleNamespace(
        mutation=_unexpected_scope,
        terminal_cleanup=lambda _retired: _yield_scope(expired_scope),
    )
    expired_store = RuntimeTransactionStore(store._policy, expired_gate)

    with pytest.raises(TerminalEnvelopeCleanupError, match="清理"):
        expired_store.clear_terminal_envelope_if_current_tombstone(retired)

    assert active_recovery_envelope_path(root).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test存活伪造mutation_scope不能写入终态证据(tmp_path: Path) -> None:
    """伪 gate 不能以真实 DTO 和旧 proof 替代尚未释放的部署锁。"""
    store, gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    with gate.mutation(proof) as trusted_scope:
        snapshot = trusted_scope.snapshot
        lineage = trusted_scope.control_lease_lineage
    with store._policy.root_binding.bind() as bound_root:
        with deployment_lock_at(bound_root) as revoked_lock:
            pass
        fake_scope = RuntimeMutationScope(
            snapshot=snapshot,
            bound_root=bound_root,
            control_lease_lineage=lineage,
            deployment_lock=revoked_lock,
        )
        fake_gate = SimpleNamespace(
            mutation=lambda _proof: _yield_scope(fake_scope),
            terminal_cleanup=_unexpected_scope,
        )
        fake_store = RuntimeTransactionStore(store._policy, fake_gate)

        with pytest.raises(RuntimeTransactionControlledStoreError):
            fake_store.write_terminal_evidence_once(evidence, proof=proof)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test存活伪造cleanup_scope不能删除活动envelope(tmp_path: Path) -> None:
    """伪 gate 不能用同 attempt 的伪造 tombstone 删除活动 envelope。"""
    store, _gate, root, _active, _completed, _evidence, _proof, retired = _complete_and_retire(
        tmp_path
    )
    fake_record = dataclasses.replace(
        retired.record,
        retired_at="2026-07-19T10:11:00Z",
    )
    fake_retired = ControlLeaseSnapshot(
        record=fake_record,
        sha256=control_lease_record_sha256(fake_record),
    )
    with store._policy.root_binding.bind() as bound_root:
        with deployment_lock_at(bound_root) as revoked_lock:
            pass
        fake_scope = RuntimeTerminalCleanupScope(
            snapshot=fake_retired,
            bound_root=bound_root,
            deployment_lock=revoked_lock,
        )
        fake_gate = SimpleNamespace(
            mutation=_unexpected_scope,
            terminal_cleanup=lambda _retired: _yield_scope(fake_scope),
        )
        fake_store = RuntimeTransactionStore(store._policy, fake_gate)

        with pytest.raises(TerminalEnvelopeCleanupError):
            fake_store.clear_terminal_envelope_if_current_tombstone(fake_retired)

    assert active_recovery_envelope_path(root).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test受控journal写入将scope_loader和原子写入复用同一bound_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """journal 路径不能把 gate 注入的能力降级为新的路径根。"""
    store, gate, _root, active, _completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )
    desired = _append_action_for_current_lease(active, proof)
    scope_roots: list[object] = []
    loader_roots: list[object] = []
    write_roots: list[object] = []
    original_mutation = ControlLeaseStore.mutation
    original_context = store._loader.load_active_context
    original_head = store._loader.load_journal_head
    original_write = controlled_store_module.write_managed_bytes_atomic_at

    @contextmanager
    def observe_mutation(
        current_gate: ControlLeaseStore,
        current_proof: object,
    ) -> Iterator[object]:
        with original_mutation(current_gate, current_proof) as scope:
            scope_roots.append(scope.bound_root)
            yield scope

    def observe_context(*, bound_root: object | None = None) -> object:
        loader_roots.append(bound_root)
        return original_context(bound_root=bound_root)

    def observe_head(
        attempt_id: str,
        *,
        bound_root: object | None = None,
    ) -> object:
        loader_roots.append(bound_root)
        return original_head(attempt_id, bound_root=bound_root)

    def observe_write(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        write_roots.append(root)
        return original_write(path, payload, root=root, policy=policy)

    monkeypatch.setattr(ControlLeaseStore, "mutation", observe_mutation)
    monkeypatch.setattr(store._loader, "load_active_context", observe_context)
    monkeypatch.setattr(store._loader, "load_journal_head", observe_head)
    monkeypatch.setattr(
        controlled_store_module,
        "write_managed_bytes_atomic_at",
        observe_write,
    )

    store.write_journal(
        desired,
        proof=proof,
        expected_sha256=transaction_journal_sha256(active),
    )

    assert len(scope_roots) == 1
    assert loader_roots
    assert write_roots
    expected = scope_roots[0]
    assert all(root is expected for root in loader_roots + write_roots)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test完成journal写入将scope证据读取和原子写入复用同一bound_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """completed 分支新增的 evidence/attempt 读取也不得脱离当前 scope。"""
    store, _gate, _root, active, completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    scope_roots: list[object] = []
    loader_roots: list[object] = []
    io_roots: list[object] = []
    original_mutation = ControlLeaseStore.mutation
    original_context = store._loader.load_active_context
    original_head = store._loader.load_journal_head
    original_evidence = controlled_store_module.load_terminal_evidence
    original_attempt = controlled_store_module.load_attempt
    original_write = controlled_store_module.write_managed_bytes_atomic_at

    @contextmanager
    def observe_mutation(
        current_gate: ControlLeaseStore,
        current_proof: object,
    ) -> Iterator[object]:
        with original_mutation(current_gate, current_proof) as scope:
            scope_roots.append(scope.bound_root)
            yield scope

    def observe_context(*, bound_root: object | None = None) -> object:
        loader_roots.append(bound_root)
        return original_context(bound_root=bound_root)

    def observe_head(
        attempt_id: str,
        *,
        bound_root: object | None = None,
    ) -> object:
        loader_roots.append(bound_root)
        return original_head(attempt_id, bound_root=bound_root)

    def observe_evidence(
        root_path: object,
        attempt_id: str,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_evidence(root_path, attempt_id, root, policy)

    def observe_attempt(
        root_path: object,
        attempt_id: str,
        reservation: object,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_attempt(root_path, attempt_id, reservation, root, policy)

    def observe_write(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_write(path, payload, root=root, policy=policy)

    monkeypatch.setattr(ControlLeaseStore, "mutation", observe_mutation)
    monkeypatch.setattr(store._loader, "load_active_context", observe_context)
    monkeypatch.setattr(store._loader, "load_journal_head", observe_head)
    monkeypatch.setattr(
        controlled_store_module,
        "load_terminal_evidence",
        observe_evidence,
    )
    monkeypatch.setattr(controlled_store_module, "load_attempt", observe_attempt)
    monkeypatch.setattr(
        controlled_store_module,
        "write_managed_bytes_atomic_at",
        observe_write,
    )

    store.write_journal(
        completed,
        proof=proof,
        expected_sha256=transaction_journal_sha256(active),
    )

    assert len(scope_roots) == 1
    assert loader_roots
    assert io_roots
    expected = scope_roots[0]
    assert all(root is expected for root in loader_roots + io_roots)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test终态清理将scope_loader和关键at原语复用同一bound_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """cleanup 的严格读、精确删和 gate 必须共享同一个受绑定根。"""
    store, gate, _root, _active, _completed, _evidence, _proof, retired = _complete_and_retire(
        tmp_path
    )
    scope_roots: list[object] = []
    loader_roots: list[object] = []
    io_roots: list[object] = []
    original_cleanup = ControlLeaseStore.terminal_cleanup
    original_evidence = controlled_store_module.load_terminal_evidence
    original_attempt = controlled_store_module.load_attempt
    original_optional = controlled_store_module.read_optional_managed_bytes_at
    original_remove = controlled_store_module.remove_managed_bytes_exact_at

    @contextmanager
    def observe_cleanup(
        current_gate: ControlLeaseStore,
        tombstone: object,
    ) -> Iterator[object]:
        with original_cleanup(current_gate, tombstone) as scope:
            scope_roots.append(scope.bound_root)
            yield scope

    def observe_loader(name: str) -> None:
        original = getattr(store._loader, name)

        def wrapped(*args: object, **kwargs: object) -> object:
            loader_roots.append(kwargs.get("bound_root"))
            return original(*args, **kwargs)

        monkeypatch.setattr(store._loader, name, wrapped)

    def observe_evidence(
        root_path: object,
        attempt_id: str,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_evidence(root_path, attempt_id, root, policy)

    def observe_attempt(
        root_path: object,
        attempt_id: str,
        reservation: object,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_attempt(root_path, attempt_id, reservation, root, policy)

    def observe_optional(
        path: Path,
        *,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_optional(path, root=root, policy=policy)

    def observe_remove(
        path: Path,
        expected: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        io_roots.append(root)
        return original_remove(path, expected, root=root, policy=policy)

    monkeypatch.setattr(ControlLeaseStore, "terminal_cleanup", observe_cleanup)
    for method_name in (
        "load_journal_head",
        "load_reservation",
        "load_derived_journal_genesis",
        "load_attempt_envelope_if_present",
        "load_active_context",
    ):
        observe_loader(method_name)
    monkeypatch.setattr(
        controlled_store_module,
        "load_terminal_evidence",
        observe_evidence,
    )
    monkeypatch.setattr(controlled_store_module, "load_attempt", observe_attempt)
    monkeypatch.setattr(
        controlled_store_module,
        "read_optional_managed_bytes_at",
        observe_optional,
    )
    monkeypatch.setattr(
        controlled_store_module,
        "remove_managed_bytes_exact_at",
        observe_remove,
    )

    store.clear_terminal_envelope_if_current_tombstone(retired)

    assert len(scope_roots) == 1
    assert loader_roots
    assert io_roots
    expected = scope_roots[0]
    assert all(root is expected for root in loader_roots + io_roots)
