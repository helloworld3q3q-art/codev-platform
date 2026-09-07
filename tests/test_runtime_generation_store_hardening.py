"""generation store 的状态证据、文件边界与失效 scope 对抗测试。"""

from __future__ import annotations

import os
from dataclasses import replace
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.runtime_attempt_contract import AttemptReservation
from codev_platform.runtime_fencing import (
    control_lease_record_sha256,
    issue_control_lease,
)
from codev_platform.runtime_generation_state import generation_state_sha256
from codev_platform.runtime_generation_store import (
    RuntimeGenerationStore,
    RuntimeGenerationStoreError,
)
from codev_platform.runtime_storage import (
    acceptance_record_path,
    deployment_lock_at,
    generation_record_path,
    generation_state_path,
    serving_permit_stage_path,
)
from codev_platform.runtime_store_protocols import (
    ControlLeaseSnapshot,
    RuntimeMutationScope,
)
from tests.runtime_generation_store_support import (
    build_generation_store_input,
    build_serving_publication_input,
    build_state_transition_input,
)


_POSIX = os.name == "posix"


@contextmanager
def _yield_scope(scope: object) -> Iterator[object]:
    """以已失效 scope 模拟错误的 gate 实现。"""
    yield scope


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test状态读取拒绝缺失截断符号链接和非普通文件(
    tmp_path: Path,
) -> None:
    """state 不是可选配置；任何非严格叶子都必须维持维护门禁。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    path = generation_state_path(inputs.root)
    outside = tmp_path / "outside-state.json"

    path.unlink()
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.load_state()
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            generation_state_sha256(publication.staged_state),
            publication.public_state,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )
    assert not path.exists()

    path.write_bytes(b"{")
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.load_state()

    path.unlink()
    outside.write_bytes(b"outside")
    path.symlink_to(outside)
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.load_state()
    assert outside.read_bytes() == b"outside"

    path.unlink()
    path.mkdir()
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.load_state()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test状态CAS拒绝陈旧摘要且保持当前state不变(tmp_path: Path) -> None:
    """先比较旧摘要，避免失败调用写入任何伪造后继。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    stale_sha256 = "0" * 64
    assert stale_sha256 != generation_state_sha256(transitions.baseline_state)

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            stale_sha256,
            transitions.switching_state,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == transitions.baseline_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit拒绝非GenerationState目标且不创建stage(tmp_path: Path) -> None:
    """target_state 必须在读取或写入 evidence 前映射为稳定 store 错误。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_serving_permit_once(
            publication.permit,
            target_state=object(),
            proof=inputs.proof,
        )

    assert not serving_permit_stage_path(
        inputs.root,
        publication.permit.attempt_id,
        generation_state_sha256(publication.staged_state),
    ).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testgeneration和acceptance的同路径异值重试不会覆盖首写(
    tmp_path: Path,
) -> None:
    """路径身份固定的不可变记录只能接受逐字节相同的恢复重试。"""
    inputs = build_generation_store_input(tmp_path)
    first_generation = inputs.store.write_generation_once(
        inputs.generation,
        proof=inputs.proof,
    )
    drifted_generation = replace(
        inputs.generation,
        created_at="2026-07-21T10:00:01Z",
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_generation_once(
            drifted_generation,
            proof=inputs.proof,
        )

    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    first_acceptance = inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    drifted_acceptance = replace(
        inputs.acceptance,
        entrypoint_proof_sha256="a" * 64,
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_acceptance_once(
            drifted_acceptance,
            fence=inputs.fence,
            proof=inputs.proof,
        )

    assert inputs.store.load_generation(inputs.generation.generation_id) == first_generation.value
    assert acceptance_record_path(inputs.root, inputs.acceptance.attempt_id).exists()
    assert first_acceptance.value == inputs.acceptance


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testacceptance拒绝身份时间谱系和接管窗口漂移(
    tmp_path: Path,
) -> None:
    """acceptance 必须同时绑定当前 attempt、generation、fence 时间和 lease 谱系。"""
    inputs = build_generation_store_input(tmp_path)
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    invalid_acceptances = (
        replace(inputs.acceptance, attempt_id="b" * 32),
        replace(inputs.acceptance, generation_id="b" * 64),
        replace(inputs.acceptance, accepted_at=inputs.fence.issued_at),
        replace(
            inputs.acceptance,
            control_lease_record_sha256_audit="a" * 64,
        ),
    )

    for acceptance in invalid_acceptances:
        with pytest.raises(RuntimeGenerationStoreError):
            inputs.store.write_acceptance_once(
                acceptance,
                fence=inputs.fence,
                proof=inputs.proof,
            )

    current = inputs.gate.load_current()
    assert current is not None
    _next, recovered_proof = inputs.gate.take_over(
        current,
        owner="generation-acceptance-recovery",
        token=bytes(range(65, 97)),
        issued_at="2026-07-21T10:00:09Z",
    )
    post_takeover_acceptance = replace(
        inputs.acceptance,
        accepted_at="2026-07-21T10:00:10Z",
    )
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_acceptance_once(
            post_takeover_acceptance,
            fence=inputs.fence,
            proof=recovered_proof,
        )

    assert not acceptance_record_path(inputs.root, inputs.acceptance.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test状态CAS拒绝版本跳跃和时间回退(tmp_path: Path) -> None:
    """单步 state 版本和单调时间是重放保护，不能由调用方放宽。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    invalid_states = (
        replace(
            transitions.switching_state,
            state_version=transitions.baseline_state.state_version + 2,
        ),
        replace(
            transitions.switching_state,
            updated_at=transitions.baseline_state.updated_at,
        ),
    )

    for desired in invalid_states:
        with pytest.raises(RuntimeGenerationStoreError):
            inputs.store.compare_and_swap_state(
                generation_state_sha256(transitions.baseline_state),
                desired,
                fence=None,
                permit=None,
                proof=inputs.proof,
            )
        assert inputs.store.load_state().state == transitions.baseline_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test验证态提交缺少显式持久fence时CAS闭锁(tmp_path: Path) -> None:
    """validating 不能靠 acceptance 摘要或目录扫描推断 commit 所用围栏。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
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

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            validating.sha256,
            transitions.staged_state,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == transitions.validating_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test验证态提交拒绝仅传入而未持久化的fence(
    tmp_path: Path,
) -> None:
    """commit 必须精确回读 O_EXCL fence，不能相信调用方内存对象。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
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

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            validating.sha256,
            transitions.staged_state,
            fence=inputs.fence,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == transitions.validating_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test公开状态CAS缺少permit时保持维护门禁关闭(tmp_path: Path) -> None:
    """公开 B 只能读取精确持久 permit，不能由状态字段自行证明。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            generation_state_sha256(publication.staged_state),
            publication.public_state,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == publication.staged_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test公开状态CAS拒绝缺失维护态A的permit_stage锚点(
    tmp_path: Path,
) -> None:
    """内容寻址 permit 不能单独代表唯一 staged publication 决定。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    permit = inputs.store.write_serving_permit_once(
        publication.permit,
        target_state=publication.public_state,
        proof=inputs.proof,
    )
    serving_permit_stage_path(
        inputs.root,
        permit.value.attempt_id,
        generation_state_sha256(publication.staged_state),
    ).unlink()

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            generation_state_sha256(publication.staged_state),
            publication.public_state,
            fence=None,
            permit=permit.value,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == publication.staged_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test失效scope在触及任何generation叶子前被拒绝(tmp_path: Path) -> None:
    """store 不得接受已离开 gate 临界区的 BoundRuntimeRoot 能力。"""
    inputs = build_generation_store_input(tmp_path)
    with inputs.gate.mutation(inputs.proof) as scope:
        expired_scope = scope
    expired_gate = SimpleNamespace(
        mutation=lambda _proof: _yield_scope(expired_scope),
    )
    expired_store = RuntimeGenerationStore(inputs.policy, expired_gate)

    with pytest.raises(RuntimeGenerationStoreError):
        expired_store.write_generation_once(
            inputs.generation,
            proof=inputs.proof,
        )

    assert not generation_record_path(
        inputs.root,
        inputs.generation.generation_id,
    ).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test存活伪造gate不能用自洽scope绕过持久current租约(
    tmp_path: Path,
) -> None:
    """依赖注入的 gate 不能把未持久化 capability 伪装为真实控制权。"""
    inputs = build_generation_store_input(tmp_path)
    reservation = AttemptReservation(
        schema_version=inputs.attempt.schema_version,
        attempt_id=inputs.attempt.attempt_id,
        operation=inputs.attempt.operation,
        plan_sha256=inputs.attempt.plan_sha256,
        controller_sha256=inputs.attempt.controller_sha256,
        created_at=inputs.attempt.created_at,
    )
    fake_record, fake_proof = issue_control_lease(
        reservation,
        epoch=1,
        token=bytes(range(97, 129)),
        owner="forged-generation-controller",
        issued_at="2026-07-21T10:00:03Z",
    )

    with inputs.policy.root_binding.bind() as root:
        with deployment_lock_at(root) as revoked_lock:
            pass
        fake_scope = RuntimeMutationScope(
            snapshot=ControlLeaseSnapshot(
                record=fake_record,
                sha256=control_lease_record_sha256(fake_record),
            ),
            bound_root=root,
            control_lease_lineage=(fake_record,),
            deployment_lock=revoked_lock,
        )
        fake_gate = SimpleNamespace(
            mutation=lambda _proof: _yield_scope(fake_scope),
        )
        fake_store = RuntimeGenerationStore(inputs.policy, fake_gate)

        with pytest.raises(RuntimeGenerationStoreError):
            fake_store.write_generation_once(
                inputs.generation,
                proof=fake_proof,
            )

    assert not generation_record_path(
        inputs.root,
        inputs.generation.generation_id,
    ).exists()
