"""受控 transaction post-lease 持久化的最小回归。"""

from __future__ import annotations

import dataclasses
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import codev_platform.runtime_transaction_controlled_store as controlled_store_module
from codev_platform import runtime_transaction_contract as transaction
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_fencing import ControlLeaseProof
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    write_managed_bytes_atomic,
)
from codev_platform.runtime_recovery_contract import create_recovery_envelope
from codev_platform.runtime_recovery_contract import encode_recovery_envelope
from codev_platform.runtime_store_protocols import RuntimeStorePolicy
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_terminal_evidence_path,
)
from codev_platform.runtime_transaction_controlled_store import (
    RuntimeTransactionControlledStoreError,
    RuntimeTransactionStore,
)
from codev_platform.runtime_transaction_contract import (
    TransactionJournalStatus,
    create_transaction_journal,
    transaction_journal_sha256,
)
from codev_platform.runtime_transaction_terminal_evidence import (
    encode_transaction_terminal_evidence,
)
from tests.runtime_transaction_controlled_store_support import (
    build_terminal_store_input as _terminal_store_input,
)
from tests.runtime_transaction_terminal_support import (
    _reservation,
    _safety_terminal_input,
)


_POSIX = os.name == "posix"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test终态证据先于completed_journal持久化且孤儿证据不代表完成(
    tmp_path: Path,
) -> None:
    store, _gate, _root, active_journal, _completed, evidence, proof = _terminal_store_input(
        tmp_path
    )

    store.write_terminal_evidence_once(evidence, proof=proof)

    assert store.load_terminal_evidence(active_journal.attempt_id) == evidence
    assert store.load_journal(active_journal.attempt_id).status is TransactionJournalStatus.ACTIVE


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test完成journal退休后只删除精确旧active_envelope且可幂等重试(
    tmp_path: Path,
) -> None:
    store, gate, root, active, completed, evidence, proof = _terminal_store_input(tmp_path)

    store.write_terminal_evidence_once(evidence, proof=proof)
    stored = store.write_journal(
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

    assert stored.value == completed
    store.clear_terminal_envelope_if_current_tombstone(retired)
    assert not active_recovery_envelope_path(root).exists()
    assert store.load_terminal_evidence(active.attempt_id) == evidence
    store.clear_terminal_envelope_if_current_tombstone(retired)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test完成journal没有先行evidence时闭锁(
    tmp_path: Path,
) -> None:
    store, _gate, _root, active, completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )

    with pytest.raises(RuntimeError):
        store.write_journal(
            completed,
            proof=proof,
            expected_sha256=transaction_journal_sha256(active),
        )

    assert store.load_journal(active.attempt_id).status is TransactionJournalStatus.ACTIVE


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testjournal_cas拒绝持正确摘要回退既有动作(
    tmp_path: Path,
) -> None:
    store, _gate, _root, active, _completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )
    invalid_successor = create_transaction_journal(
        _reservation(),
        created_at=active.created_at,
    )

    with pytest.raises(RuntimeError):
        store.write_journal(
            invalid_successor,
            proof=proof,
            expected_sha256=transaction_journal_sha256(active),
        )

    assert store.load_journal(active.attempt_id) == active


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpostlease_envelope入口只能确认bootstrap且拒绝重复发布(
    tmp_path: Path,
) -> None:
    store, _gate, _root, active, _completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )
    reservation = _reservation()
    envelope = create_recovery_envelope(
        reservation,
        create_transaction_journal(reservation, created_at=active.created_at),
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="d" * 64,
        transaction_store_id="runtime-postlease",
        created_at=active.created_at,
    )

    assert store.write_envelope_once(envelope, proof=proof).value == envelope
    with pytest.raises(RuntimeError):
        store.publish_active_envelope_if_absent(envelope, proof=proof)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test受控首写只使用gate注入的同一bound_root且不自行绑定(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, gate, _root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    policy = store._policy
    original_bind = type(policy.root_binding).bind
    original_mutation = ControlLeaseStore.mutation
    original_context = store._loader.load_active_context
    original_attempt = controlled_store_module.load_attempt
    original_optional = controlled_store_module.read_optional_managed_bytes_at
    original_create = controlled_store_module.create_managed_bytes_exclusive_at
    bound_roots: list[object] = []
    scope_roots: list[object] = []
    observed_roots: list[object] = []
    bind_calls = 0

    @contextmanager
    def observe_bind(binding: object) -> Iterator[object]:
        nonlocal bind_calls
        bind_calls += 1
        with original_bind(binding) as root:
            yield root

    @contextmanager
    def observe_mutation(
        current_gate: ControlLeaseStore,
        current_proof: ControlLeaseProof,
    ) -> Iterator[object]:
        with original_mutation(current_gate, current_proof) as scope:
            scope_roots.append(scope.bound_root)
            yield scope

    def observe_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        bound_roots.append(root)
        return original_create(path, payload, root=root, policy=policy)

    def observe_context(*, bound_root: object | None = None) -> object:
        observed_roots.append(bound_root)
        return original_context(bound_root=bound_root)

    def observe_attempt(
        root_path: object,
        attempt_id: str,
        reservation: object,
        root: object,
        policy: object,
    ) -> object:
        observed_roots.append(root)
        return original_attempt(root_path, attempt_id, reservation, root, policy)

    def observe_optional(
        path: Path,
        *,
        root: object,
        policy: object,
    ) -> object:
        observed_roots.append(root)
        return original_optional(path, root=root, policy=policy)

    monkeypatch.setattr(type(policy.root_binding), "bind", observe_bind)
    monkeypatch.setattr(ControlLeaseStore, "mutation", observe_mutation)
    monkeypatch.setattr(store._loader, "load_active_context", observe_context)
    monkeypatch.setattr(controlled_store_module, "load_attempt", observe_attempt)
    monkeypatch.setattr(
        controlled_store_module,
        "read_optional_managed_bytes_at",
        observe_optional,
    )
    monkeypatch.setattr(
        controlled_store_module,
        "create_managed_bytes_exclusive_at",
        observe_create,
    )

    store.write_terminal_evidence_once(evidence, proof=proof)

    assert bind_calls == 1
    assert bound_roots == scope_roots
    assert observed_roots
    assert all(root is scope_roots[0] for root in observed_roots)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test不同policy的同路径scope不能写入本store(
    tmp_path: Path,
) -> None:
    store, _gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    foreign_policy = RuntimeStorePolicy(root=root, owner_uid=os.geteuid())
    foreign_gate = ControlLeaseStore(foreign_policy)
    foreign_store = RuntimeTransactionStore(store._policy, foreign_gate)

    with pytest.raises(RuntimeError):
        foreign_store.write_terminal_evidence_once(evidence, proof=proof)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test终态清理遇到漂移active_envelope时不能删除它(
    tmp_path: Path,
) -> None:
    store, gate, root, active, completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
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
    reservation = _reservation()
    drifted = create_recovery_envelope(
        reservation,
        create_transaction_journal(reservation, created_at=active.created_at),
        controller_root_relative="controller",
        interpreter_relative="controller/bin/python",
        interpreter_sha256="e" * 64,
        transaction_store_id="runtime-postlease",
        created_at=active.created_at,
    )
    path = active_recovery_envelope_path(root)
    payload = encode_recovery_envelope(drifted)
    write_managed_bytes_atomic(
        path,
        payload,
        root=root,
        policy=ManagedFilePolicy(
            mode=0o600,
            require_uid=os.geteuid(),
            max_bytes=32_768,
        ),
    )

    with pytest.raises(RuntimeError):
        store.clear_terminal_envelope_if_current_tombstone(retired)

    assert path.read_bytes() == payload


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test无proof读取拒绝evidence与冻结attempt摘要漂移(
    tmp_path: Path,
) -> None:
    store, _gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    drifted = dataclasses.replace(evidence, deployment_attempt_sha256="f" * 64)
    write_managed_bytes_atomic(
        attempt_terminal_evidence_path(root, evidence.attempt_id),
        encode_transaction_terminal_evidence(drifted),
        root=root,
        policy=ManagedFilePolicy(
            mode=0o600,
            require_uid=os.geteuid(),
            max_bytes=32_768,
        ),
    )

    with pytest.raises(RuntimeTransactionControlledStoreError, match="transaction"):
        store.load_terminal_evidence(evidence.attempt_id)


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test无proof读取仅自行绑定一次且不进入gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _gate, _root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    store.write_terminal_evidence_once(evidence, proof=proof)
    policy = store._policy
    original_bind = type(policy.root_binding).bind
    bind_calls = 0

    @contextmanager
    def observe_bind(binding: object) -> Iterator[object]:
        nonlocal bind_calls
        bind_calls += 1
        with original_bind(binding) as root:
            yield root

    def fail_gate(*args: object, **kwargs: object) -> object:
        raise AssertionError("无 proof 读取不得进入 control gate")

    monkeypatch.setattr(type(policy.root_binding), "bind", observe_bind)
    monkeypatch.setattr(ControlLeaseStore, "mutation", fail_gate)
    monkeypatch.setattr(ControlLeaseStore, "terminal_cleanup", fail_gate)

    assert store.load_terminal_evidence(evidence.attempt_id) == evidence
    assert bind_calls == 1


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test根替换发生在受控evidence首写前时不写替换命名根(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store, _gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    replacement = tmp_path / "replacement"
    shutil.copytree(root, replacement)
    original_create = controlled_store_module.create_managed_bytes_exclusive_at
    swapped = False

    def replace_before_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        nonlocal swapped
        previous = root_path.with_name(f"{root_path.name}-previous")
        root_path.rename(previous)
        replacement.rename(root_path)
        swapped = True
        return original_create(path, payload, root=root, policy=policy)

    root_path = root
    monkeypatch.setattr(
        controlled_store_module,
        "create_managed_bytes_exclusive_at",
        replace_before_create,
    )

    with pytest.raises(RuntimeError):
        store.write_terminal_evidence_once(evidence, proof=proof)

    assert swapped
    assert not attempt_terminal_evidence_path(root_path, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test接管后的旧proof不能再写入terminal_evidence(
    tmp_path: Path,
) -> None:
    store, gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    current = gate.load_current()
    assert current is not None
    gate.take_over(
        current,
        owner="recovery-a",
        token=bytes(range(32, 64)),
        issued_at="2026-07-19T10:12:00Z",
    )

    with pytest.raises(RuntimeError):
        store.write_terminal_evidence_once(evidence, proof=proof)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test首次terminal_evidence不能伪造或锚定旧control_lease(
    tmp_path: Path,
) -> None:
    store, _gate, root, _active, _completed, evidence, proof = _terminal_store_input(
        tmp_path,
    )
    forged = dataclasses.replace(
        evidence,
        control_lease_epoch_audit=2,
        control_token_sha256_audit="f" * 64,
        completion_control_lease_sha256="e" * 64,
    )

    with pytest.raises(RuntimeError):
        store.write_terminal_evidence_once(forged, proof=proof)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testjournal新动作必须审计当前scope的control_lease(
    tmp_path: Path,
) -> None:
    store, _gate, _root, active, _completed, _evidence, proof = _terminal_store_input(
        tmp_path,
    )
    attempt, _journal, record, _expected_proof, _state, _acceptance, _fence = (
        _safety_terminal_input()
    )
    valid_action = transaction.prepare_transaction_action(
        attempt,
        record,
        proof,
        step_sequence=3,
        intent=transaction.TransactionActionIntent.APPLY_RESOURCE,
        resource_kind="generation-state",
        resource_id="secondary",
        operation_sha256="1" * 64,
        before_sha256="2" * 64,
        after_sha256="3" * 64,
        recorded_at="2026-07-19T10:12:00Z",
    )
    forged_action = dataclasses.replace(
        valid_action,
        control_lease_epoch_audit=2,
        control_token_sha256_audit="e" * 64,
        control_lease_record_sha256_audit="f" * 64,
    )
    forged_journal = dataclasses.replace(
        active,
        actions=active.actions + (forged_action,),
        updated_at=forged_action.recorded_at,
    )

    with pytest.raises(RuntimeError):
        store.write_journal(
            forged_journal,
            proof=proof,
            expected_sha256=transaction_journal_sha256(active),
        )

    assert store.load_journal(active.attempt_id) == active
