"""transaction 受控写入与清理的跨进程、完整锁生命周期回归。"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import codev_platform.runtime_transaction_controlled_store as controlled_store_module
from codev_platform.runtime_storage import (
    active_recovery_envelope_path,
    attempt_terminal_evidence_path,
    deployment_lock_at,
)
from codev_platform.runtime_store_protocols import (
    RuntimeMutationScope,
    RuntimeTerminalCleanupScope,
)
from codev_platform.runtime_transaction_controlled_store import (
    RuntimeTransactionControlledStoreError,
    RuntimeTransactionStore,
    TerminalEnvelopeCleanupError,
)
from codev_platform.runtime_transaction_contract import transaction_journal_sha256
from tests.runtime_transaction_controlled_store_support import (
    build_terminal_store_input,
)


_POSIX = os.name == "posix"


@contextmanager
def _yield_scope(scope: object) -> Iterator[object]:
    """以测试 gate 返回既有 scope，但不自行签发 capability。"""
    yield scope


def _unexpected_scope(*args: object, **kwargs: object) -> Iterator[object]:
    """禁止测试路径意外进入未验证的另一类 gate。"""
    del args, kwargs
    raise AssertionError("不应进入该 gate")
    yield object()


def _complete_and_retire(tmp_path: Path):
    """构造仍保留 active envelope 的真实 completed tombstone。"""
    store, gate, root, active, completed, evidence, proof = build_terminal_store_input(
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
        retired_at="2026-07-21T00:00:04Z",
    )
    return store, gate, root, active, completed, evidence, proof, retired


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 与受管 flock 仅在 WSL/Linux 验证",
)
def testfork子进程不能重放父进程释放后的terminal_evidence_scope(
    tmp_path: Path,
) -> None:
    """父进程离开 mutation 后，child 继承的 scope 必须在任何写入前失效。"""
    store, gate, root, _active, _completed, evidence, proof = build_terminal_store_input(
        tmp_path,
    )
    start_read, start_write = os.pipe()
    result_read, result_write = os.pipe()
    child_pid: int | None = None
    try:
        with gate.mutation(proof) as scope:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(start_write)
                os.close(result_read)
                try:
                    assert os.read(start_read, 2) == b"go"
                    fake_gate = SimpleNamespace(
                        mutation=lambda _proof: _yield_scope(scope),
                        terminal_cleanup=_unexpected_scope,
                    )
                    fake_store = RuntimeTransactionStore(store._policy, fake_gate)
                    try:
                        fake_store.write_terminal_evidence_once(evidence, proof=proof)
                    except RuntimeTransactionControlledStoreError:
                        os.write(result_write, b"blocked")
                    else:
                        os.write(result_write, b"accepted")
                except BaseException:
                    os.write(result_write, b"error")
                finally:
                    os.close(start_read)
                    os.close(result_write)
                os._exit(0)
        os.close(start_read)
        os.close(result_write)
        assert child_pid is not None
        assert os.write(start_write, b"go") == 2
        assert os.read(result_read, 16) == b"blocked"
        _pid, status = os.waitpid(child_pid, 0)
        child_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for descriptor in (start_read, start_write, result_read, result_write):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if child_pid is not None:
            _pid, _status = os.waitpid(child_pid, 0)

    assert not attempt_terminal_evidence_path(root, evidence.attempt_id).exists()


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 与受管 flock 仅在 WSL/Linux 验证",
)
def testfork子进程不能重放父进程释放后的terminal_cleanup_scope(
    tmp_path: Path,
) -> None:
    """父进程离开 cleanup 后，child 继承的 tombstone scope 不得删除 envelope。"""
    store, gate, root, _active, _completed, _evidence, _proof, retired = _complete_and_retire(
        tmp_path
    )
    start_read, start_write = os.pipe()
    result_read, result_write = os.pipe()
    child_pid: int | None = None
    try:
        with gate.terminal_cleanup(retired) as scope:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(start_write)
                os.close(result_read)
                try:
                    assert os.read(start_read, 2) == b"go"
                    fake_gate = SimpleNamespace(
                        mutation=_unexpected_scope,
                        terminal_cleanup=lambda _retired: _yield_scope(scope),
                    )
                    fake_store = RuntimeTransactionStore(store._policy, fake_gate)
                    try:
                        fake_store.clear_terminal_envelope_if_current_tombstone(retired)
                    except TerminalEnvelopeCleanupError:
                        os.write(result_write, b"blocked")
                    else:
                        os.write(result_write, b"accepted")
                except BaseException:
                    os.write(result_write, b"error")
                finally:
                    os.close(start_read)
                    os.close(result_write)
                os._exit(0)
        os.close(start_read)
        os.close(result_write)
        assert child_pid is not None
        assert os.write(start_write, b"go") == 2
        assert os.read(result_read, 16) == b"blocked"
        _pid, status = os.waitpid(child_pid, 0)
        child_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for descriptor in (start_read, start_write, result_read, result_write):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if child_pid is not None:
            _pid, _status = os.waitpid(child_pid, 0)

    assert active_recovery_envelope_path(root).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testterminal_evidence写入期间capability阻止异步提前释放(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """deployment-lock 吊销必须等待 evidence 的最终 O_EXCL 写入结束。"""
    store, gate, _root, _active, _completed, evidence, proof = build_terminal_store_input(
        tmp_path,
    )
    with gate.mutation(proof) as trusted_scope:
        snapshot = trusted_scope.snapshot
        lineage = trusted_scope.control_lease_lineage
    ready = Event()
    release_requested = Event()
    release_started = Event()
    release_completed = Event()
    holder: dict[str, object] = {}
    holder_errors: list[BaseException] = []

    def hold_real_lock() -> None:
        try:
            with store._policy.root_binding.bind() as root:
                with deployment_lock_at(root) as deployment_lock:
                    holder["scope"] = RuntimeMutationScope(
                        snapshot=snapshot,
                        bound_root=root,
                        control_lease_lineage=lineage,
                        deployment_lock=deployment_lock,
                    )
                    ready.set()
                    assert release_requested.wait(timeout=5)
                    release_started.set()
        except BaseException as error:
            holder_errors.append(error)
        finally:
            release_completed.set()

    worker = Thread(target=hold_real_lock, daemon=True)
    worker.start()
    assert ready.wait(timeout=5)
    assert not holder_errors
    scope = holder["scope"]
    fake_gate = SimpleNamespace(
        mutation=lambda _proof: _yield_scope(scope),
        terminal_cleanup=_unexpected_scope,
    )
    fake_store = RuntimeTransactionStore(store._policy, fake_gate)
    original_create = controlled_store_module.create_managed_bytes_exclusive_at

    def observe_terminal_create(path, payload, *, root, policy):
        release_requested.set()
        assert release_started.wait(timeout=5)
        assert not release_completed.wait(timeout=0.1)
        return original_create(path, payload, root=root, policy=policy)

    monkeypatch.setattr(
        controlled_store_module,
        "create_managed_bytes_exclusive_at",
        observe_terminal_create,
    )
    try:
        fake_store.write_terminal_evidence_once(evidence, proof=proof)
        assert release_completed.wait(timeout=5)
    finally:
        release_requested.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not holder_errors


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testterminal_cleanup删除期间capability阻止异步提前释放(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """deployment-lock 吊销必须等待精确 envelope 删除结束。"""
    store, gate, _root, _active, _completed, _evidence, _proof, retired = _complete_and_retire(
        tmp_path
    )
    ready = Event()
    release_requested = Event()
    release_started = Event()
    release_completed = Event()
    holder: dict[str, object] = {}
    holder_errors: list[BaseException] = []

    def hold_real_lock() -> None:
        try:
            with store._policy.root_binding.bind() as root:
                with deployment_lock_at(root) as deployment_lock:
                    holder["scope"] = RuntimeTerminalCleanupScope(
                        snapshot=retired,
                        bound_root=root,
                        deployment_lock=deployment_lock,
                    )
                    ready.set()
                    assert release_requested.wait(timeout=5)
                    release_started.set()
        except BaseException as error:
            holder_errors.append(error)
        finally:
            release_completed.set()

    worker = Thread(target=hold_real_lock, daemon=True)
    worker.start()
    assert ready.wait(timeout=5)
    assert not holder_errors
    scope = holder["scope"]
    fake_gate = SimpleNamespace(
        mutation=_unexpected_scope,
        terminal_cleanup=lambda _retired: _yield_scope(scope),
    )
    fake_store = RuntimeTransactionStore(store._policy, fake_gate)
    original_remove = controlled_store_module.remove_managed_bytes_exact_at

    def observe_terminal_remove(path, expected, *, root, policy):
        release_requested.set()
        assert release_started.wait(timeout=5)
        assert not release_completed.wait(timeout=0.1)
        return original_remove(path, expected, root=root, policy=policy)

    monkeypatch.setattr(
        controlled_store_module,
        "remove_managed_bytes_exact_at",
        observe_terminal_remove,
    )
    try:
        fake_store.clear_terminal_envelope_if_current_tombstone(retired)
        assert release_completed.wait(timeout=5)
    finally:
        release_requested.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not holder_errors
