"""generation scope 的活跃 deployment-lock capability 对抗回归。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import codev_platform._runtime_generation_store_state as state_store_module
import codev_platform._runtime_generation_store_validation as validation_module
import codev_platform.runtime_deployment_lock_capability as lock_capability_module
import codev_platform.runtime_generation_store as generation_store_module
from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_deployment_lock_capability import (
    BoundDeploymentLock,
    RuntimeDeploymentLockCapabilityError,
)
from codev_platform.runtime_fencing_store import ControlLeaseStore
from codev_platform.runtime_generation_state import generation_state_sha256
from codev_platform.runtime_generation_store import (
    RuntimeGenerationStore,
    RuntimeGenerationStoreError,
)
from codev_platform.runtime_storage import (
    acceptance_record_path,
    deployment_lock_at,
    generation_record_path,
    serving_fence_record_path,
    serving_permit_record_path,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_serving_permit import serving_permit_sha256
from codev_platform.runtime_store_protocols import RuntimeMutationScope
from tests.runtime_generation_store_support import (
    build_generation_store_input,
    build_state_transition_input,
)


_POSIX = os.name == "posix"


@contextmanager
def _yield_scope(scope: object) -> Iterator[object]:
    """以注入的 scope 模拟错误 gate；该 gate 自身不持有部署锁。"""
    yield scope


def testdeploymentlock能力不暴露无锁签发或吊销入口() -> None:
    """普通依赖注入对象不能直接伪造仍活跃的锁 capability。"""
    assert not hasattr(lock_capability_module, "issue_bound_deployment_lock")
    assert not hasattr(lock_capability_module, "revoke_bound_deployment_lock")


def test未签发deploymentlock对象不能伪装为活动能力() -> None:
    """即使绕过构造器得到同类型对象，也必须因私有注册表缺席而闭锁。"""
    forged_capability = object.__new__(BoundDeploymentLock)
    forged_root = object.__new__(BoundRuntimeRoot)

    with pytest.raises(RuntimeDeploymentLockCapabilityError):
        forged_capability.require_active(forged_root)
    with pytest.raises(RuntimeDeploymentLockCapabilityError):
        with forged_capability.hold_active(forged_root):
            pass


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test伪gate复用当前真实scope数据但无活跃锁仍拒绝写入和cas(
    tmp_path,
) -> None:
    """真实 DTO 不能替代尚未释放的同根 deployment flock 能力。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    with inputs.gate.mutation(inputs.proof) as trusted_scope:
        snapshot = trusted_scope.snapshot
        lineage = trusted_scope.control_lease_lineage

    with inputs.policy.root_binding.bind() as root:
        with deployment_lock_at(root) as revoked_lock:
            pass
        fake_scope = RuntimeMutationScope(
            snapshot=snapshot,
            bound_root=root,
            control_lease_lineage=lineage,
            deployment_lock=revoked_lock,
        )
        fake_gate = SimpleNamespace(mutation=lambda _proof: _yield_scope(fake_scope))
        fake_store = RuntimeGenerationStore(inputs.policy, fake_gate)

        with pytest.raises(RuntimeGenerationStoreError):
            fake_store.write_generation_once(
                inputs.generation,
                proof=inputs.proof,
            )
        with pytest.raises(RuntimeGenerationStoreError):
            fake_store.compare_and_swap_state(
                generation_state_sha256(transitions.baseline_state),
                transitions.switching_state,
                fence=None,
                permit=None,
                proof=inputs.proof,
            )

    assert not generation_record_path(
        inputs.root,
        inputs.generation.generation_id,
    ).exists()
    assert inputs.store.load_state().state == transitions.baseline_state


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 与受管 flock 仅在 WSL/Linux 验证",
)
def testfork子进程不能重放父进程释放后的活动scope(
    tmp_path: Path,
) -> None:
    """父进程释放 flock 后，子进程继承的 scope 必须在任意写入前失效。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    start_read, start_write = os.pipe()
    result_read, result_write = os.pipe()
    child_pid: int | None = None
    try:
        with inputs.gate.mutation(inputs.proof) as scope:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(start_write)
                os.close(result_read)
                try:
                    assert os.read(start_read, 2) == b"go"
                    fake_gate = SimpleNamespace(
                        mutation=lambda _proof: _yield_scope(scope),
                    )
                    fake_store = RuntimeGenerationStore(inputs.policy, fake_gate)
                    operations = (
                        lambda: fake_store.write_generation_once(
                            inputs.generation,
                            proof=inputs.proof,
                        ),
                        lambda: fake_store.compare_and_swap_state(
                            generation_state_sha256(transitions.baseline_state),
                            transitions.switching_state,
                            fence=None,
                            permit=None,
                            proof=inputs.proof,
                        ),
                    )
                    blocked = True
                    for operation in operations:
                        try:
                            operation()
                        except RuntimeGenerationStoreError:
                            continue
                        blocked = False
                    os.write(result_write, b"blocked" if blocked else b"accepted")
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

    assert not generation_record_path(
        inputs.root,
        inputs.generation.generation_id,
    ).exists()
    assert inputs.store.load_state().state == transitions.baseline_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test活跃capability在完整写入期阻止异步gate提前释放锁(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """revoke 必须等待当前 scope 的 verifier 与最终 O_EXCL 写入全部结束。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    with inputs.gate.mutation(inputs.proof) as trusted_scope:
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
            with inputs.policy.root_binding.bind() as root:
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
    fake_gate = SimpleNamespace(mutation=lambda _proof: _yield_scope(scope))
    fake_store = RuntimeGenerationStore(inputs.policy, fake_gate)
    original_create = generation_store_module.create_managed_bytes_exclusive_at

    def observe_final_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        release_requested.set()
        assert release_started.wait(timeout=5)
        assert not release_completed.wait(timeout=0.1)
        return original_create(path, payload, root=root, policy=policy)

    monkeypatch.setattr(
        generation_store_module,
        "create_managed_bytes_exclusive_at",
        observe_final_create,
    )
    try:
        fake_store.write_generation_once(inputs.generation, proof=inputs.proof)
        assert release_completed.wait(timeout=5)
    finally:
        release_requested.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert not holder_errors


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test受控状态链的gate_loader_lock和at原语复用同一bound_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """单次 mutation 内不能把 gate 注入的根能力降级为重新按路径打开。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    active_root: list[object] = []
    observed_roots: list[object] = []
    original_mutation = ControlLeaseStore.mutation
    original_loader = inputs.store._loader.load_active_context
    original_read = validation_module.read_managed_bytes_at
    original_create = generation_store_module.create_managed_bytes_exclusive_at
    original_lock = state_store_module.activation_lock_at
    original_write = state_store_module.write_managed_bytes_atomic_at

    @contextmanager
    def observe_mutation(
        gate: ControlLeaseStore,
        proof: object,
    ) -> Iterator[object]:
        with original_mutation(gate, proof) as scope:
            active_root.append(scope.bound_root)
            try:
                yield scope
            finally:
                active_root.pop()

    def record_root(root: object) -> None:
        assert active_root and root is active_root[-1]
        observed_roots.append(root)

    def observe_loader(*, bound_root: object | None = None) -> object:
        record_root(bound_root)
        return original_loader(bound_root=bound_root)

    def observe_read(
        path: Path,
        *,
        root: object,
        policy: object,
    ) -> bytes:
        record_root(root)
        return original_read(path, root=root, policy=policy)

    def observe_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        record_root(root)
        return original_create(path, payload, root=root, policy=policy)

    @contextmanager
    def observe_lock(root: object) -> Iterator[None]:
        record_root(root)
        with original_lock(root):
            yield

    def observe_write(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        record_root(root)
        return original_write(path, payload, root=root, policy=policy)

    monkeypatch.setattr(ControlLeaseStore, "mutation", observe_mutation)
    monkeypatch.setattr(inputs.store._loader, "load_active_context", observe_loader)
    monkeypatch.setattr(validation_module, "read_managed_bytes_at", observe_read)
    monkeypatch.setattr(
        generation_store_module,
        "create_managed_bytes_exclusive_at",
        observe_create,
    )
    monkeypatch.setattr(state_store_module, "activation_lock_at", observe_lock)
    monkeypatch.setattr(
        state_store_module,
        "write_managed_bytes_atomic_at",
        observe_write,
    )

    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    switched = inputs.store.compare_and_swap_state(
        generation_state_sha256(transitions.baseline_state),
        transitions.switching_state,
        fence=None,
        permit=None,
        proof=inputs.proof,
    )
    validating = inputs.store.compare_and_swap_state(
        switched.sha256,
        transitions.validating_state,
        fence=None,
        permit=None,
        proof=inputs.proof,
    )
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    staged = inputs.store.compare_and_swap_state(
        validating.sha256,
        transitions.staged_state,
        fence=inputs.fence,
        permit=None,
        proof=inputs.proof,
    )
    permit = inputs.store.write_serving_permit_once(
        transitions.permit,
        target_state=transitions.public_state,
        proof=inputs.proof,
    )
    inputs.store.compare_and_swap_state(
        staged.sha256,
        transitions.public_state,
        fence=None,
        permit=permit.value,
        proof=inputs.proof,
    )

    assert observed_roots


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testgeneration首写遇到命名根替换时不写入替换根(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """根描述符绑定在最终 O_EXCL 前仍须拒绝可见路径被替换。"""
    inputs = build_generation_store_input(tmp_path)
    replacement = tmp_path / "replacement"
    shutil.copytree(inputs.root, replacement)
    original_create = generation_store_module.create_managed_bytes_exclusive_at
    root = inputs.root
    swapped = False

    def replace_before_create(
        path: Path,
        payload: bytes,
        *,
        root: object,
        policy: object,
    ) -> object:
        nonlocal swapped
        previous = inputs.root.with_name(f"{inputs.root.name}-previous")
        inputs.root.rename(previous)
        replacement.rename(inputs.root)
        swapped = True
        return original_create(path, payload, root=root, policy=policy)

    monkeypatch.setattr(
        generation_store_module,
        "create_managed_bytes_exclusive_at",
        replace_before_create,
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_generation_once(
            inputs.generation,
            proof=inputs.proof,
        )

    assert swapped
    assert not generation_record_path(
        root,
        inputs.generation.generation_id,
    ).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test接管后的旧proof不能创建任意generation证据叶子(
    tmp_path: Path,
) -> None:
    """旧 capability 必须在进入各写入流程前被统一 gate 拒绝。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    current = inputs.gate.load_current()
    assert current is not None
    inputs.gate.take_over(
        current,
        owner="generation-recovery",
        token=bytes(range(65, 97)),
        issued_at="2026-07-21T10:00:09Z",
    )

    operations = (
        lambda: inputs.store.write_generation_once(
            inputs.generation,
            proof=inputs.proof,
        ),
        lambda: inputs.store.write_serving_fence_once(
            inputs.fence,
            proof=inputs.proof,
        ),
        lambda: inputs.store.write_acceptance_once(
            inputs.acceptance,
            fence=inputs.fence,
            proof=inputs.proof,
        ),
        lambda: inputs.store.write_serving_permit_once(
            transitions.permit,
            target_state=transitions.public_state,
            proof=inputs.proof,
        ),
        lambda: inputs.store.compare_and_swap_state(
            generation_state_sha256(transitions.baseline_state),
            transitions.switching_state,
            fence=None,
            permit=None,
            proof=inputs.proof,
        ),
    )

    for operation in operations:
        with pytest.raises(RuntimeGenerationStoreError):
            operation()

    assert not generation_record_path(
        inputs.root,
        inputs.generation.generation_id,
    ).exists()
    assert not serving_fence_record_path(
        inputs.root,
        inputs.fence.accepted_attempt_id,
        canonical_sha256(inputs.fence),
    ).exists()
    assert not acceptance_record_path(inputs.root, inputs.acceptance.attempt_id).exists()
    assert not serving_permit_record_path(
        inputs.root,
        transitions.permit.attempt_id,
        serving_permit_sha256(transitions.permit),
    ).exists()
