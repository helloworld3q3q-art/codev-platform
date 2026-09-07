"""运行代际受控持久化入口的公开契约测试。"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import inspect
import os

import pytest

from codev_platform.runtime_generation_state import (
    GenerationMode,
    generation_state_sha256,
    mark_restricted,
    mark_safety_unproven,
    prepare_serving_publication,
)
from codev_platform.runtime_generation_acceptance import serving_binding_sha256
from codev_platform.runtime_generation_store import RuntimeGenerationStoreError
from codev_platform.runtime_serving_permit import create_serving_permit
from codev_platform.runtime_store_protocols import RuntimeMutationScope
from tests.runtime_generation_store_support import (
    build_generation_store_input,
    build_serving_publication_input,
    build_state_transition_input,
    GenerationStateRaceInput,
    run_generation_state_cas_race,
    seed_state_for_test,
)


_POSIX = os.name == "posix"


def test活动scope必须显式携带已验证的control租约谱系() -> None:
    """分域 store 不能自行扫描 history 或重新打开具体 lease store。"""
    assert "control_lease_lineage" in RuntimeMutationScope.__dataclass_fields__


def testgeneration_store必须暴露完整的不可变证据链接口() -> None:
    """公开状态不能绕过 fence、acceptance 与 target-B permit 的精确绑定。"""
    module_name = "codev_platform.runtime_generation_store"

    assert importlib.util.find_spec(module_name) is not None

    store_type = importlib.import_module(module_name).RuntimeGenerationStore
    expected_parameters = {
        "write_generation_once": ("self", "generation", "proof"),
        "load_generation": ("self", "generation_id"),
        "write_serving_fence_once": ("self", "fence", "proof"),
        "write_acceptance_once": ("self", "acceptance", "fence", "proof"),
        "write_serving_permit_once": ("self", "permit", "target_state", "proof"),
        "load_state": ("self",),
        "compare_and_swap_state": (
            "self",
            "expected_sha256",
            "desired",
            "fence",
            "permit",
            "proof",
        ),
    }

    for method_name, parameter_names in expected_parameters.items():
        method = getattr(store_type, method_name)
        assert tuple(inspect.signature(method).parameters) == parameter_names


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testgeneration只在当前控制scope内按身份不可变持久化(
    tmp_path,
) -> None:
    """generation 首写、严格读取与同值重试必须使用同一个冻结身份。"""
    inputs = build_generation_store_input(tmp_path)

    stored = inputs.store.write_generation_once(
        inputs.generation,
        proof=inputs.proof,
    )

    assert stored.value == inputs.generation
    assert inputs.store.load_generation(inputs.generation.generation_id) == inputs.generation
    assert (
        inputs.store.write_generation_once(
            inputs.generation,
            proof=inputs.proof,
        )
        == stored
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testserving围栏只能为当前attempt已持久目标generation冻结(
    tmp_path,
) -> None:
    """fence 首写必须锚定同一 scope 的 attempt 和 immutable generation。"""
    inputs = build_generation_store_input(tmp_path)
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)

    stored = inputs.store.write_serving_fence_once(
        inputs.fence,
        proof=inputs.proof,
    )

    assert stored.value == inputs.fence
    assert (
        inputs.store.write_serving_fence_once(
            inputs.fence,
            proof=inputs.proof,
        )
        == stored
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testacceptance只能在精确持久fence和当前lease谱系证明后冻结(
    tmp_path,
) -> None:
    """验收记录不能以传入对象代替持久 fence，也不能绕过 lease 审计锚点。"""
    inputs = build_generation_store_input(tmp_path)
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)

    stored = inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )

    assert stored.value == inputs.acceptance
    assert (
        inputs.store.write_acceptance_once(
            inputs.acceptance,
            fence=inputs.fence,
            proof=inputs.proof,
        )
        == stored
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit只能预绑定维护态A导出的公开状态B(
    tmp_path,
) -> None:
    """permit 不得依据传入对象猜测 fence，也不得绑定当前仍关闭入口的 A。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )

    stored = inputs.store.write_serving_permit_once(
        publication.permit,
        target_state=publication.public_state,
        proof=inputs.proof,
    )

    assert stored.value == publication.permit
    assert (
        inputs.store.write_serving_permit_once(
            publication.permit,
            target_state=publication.public_state,
            proof=inputs.proof,
        )
        == stored
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit不能在公开状态B已经持久后补写(
    tmp_path,
) -> None:
    """permit 是发布前证据，不能在维护门禁已打开后反向补造。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    seed_state_for_test(inputs, publication.public_state)
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
            target_state=publication.public_state,
            proof=inputs.proof,
        )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test同一维护态A只能预写一个精确permit(
    tmp_path,
) -> None:
    """不同 issued_at 的第二个 permit 不能制造公开证据歧义。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    first = inputs.store.write_serving_permit_once(
        publication.permit,
        target_state=publication.public_state,
        proof=inputs.proof,
    )
    alternative = dataclasses.replace(
        publication.permit,
        issued_at="2026-07-21T10:00:09Z",
    )
    alternative_public_state = prepare_serving_publication(
        publication.staged_state,
        serving_binding_sha256(inputs.acceptance),
        updated_at="2026-07-21T10:00:10Z",
    )
    alternative_target = create_serving_permit(
        alternative_public_state,
        inputs.acceptance,
        inputs.fence,
        issued_at="2026-07-21T10:00:11Z",
    )

    for candidate, target in (
        (alternative, publication.public_state),
        (alternative_target, alternative_public_state),
    ):
        with pytest.raises(RuntimeGenerationStoreError):
            inputs.store.write_serving_permit_once(
                candidate,
                target_state=target,
                proof=inputs.proof,
            )

    assert (
        inputs.store.write_serving_permit_once(
            first.value,
            target_state=publication.public_state,
            proof=inputs.proof,
        )
        == first
    )


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testCAS只接受四条既有纯状态转换函数导出的精确后继(
    tmp_path,
) -> None:
    """switch、validate、commit 与 publish 均不能由调用方手工拼装。"""
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
    published = inputs.store.compare_and_swap_state(
        staged.sha256,
        transitions.public_state,
        fence=None,
        permit=permit.value,
        proof=inputs.proof,
    )

    assert published.state == transitions.public_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test状态CAS两个真实spawn控制器竞争同一旧摘要时只有一个成功(
    tmp_path,
) -> None:
    """activation lock 与严格 expected SHA 必须共同关闭跨进程写入窗口。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)

    results = run_generation_state_cas_race(
        GenerationStateRaceInput(
            root=str(inputs.root),
            owner_uid=inputs.policy.owner_uid,
            expected_sha256=generation_state_sha256(transitions.baseline_state),
            desired=transitions.switching_state,
            proof=inputs.proof,
        ),
    )

    assert sum(result.outcome == "success" for result in results) == 1
    assert sum(result.outcome == "conflict" for result in results) == 1
    assert inputs.store.load_state().state == transitions.switching_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testCAS危险状态只能由既有收敛函数推进且safety终态不可退出(
    tmp_path,
) -> None:
    """restricted 和 safety_unproven 不是可由调用方随意构造的恢复通道。"""
    transitions = build_state_transition_input(tmp_path)
    inputs = transitions.base
    restricted = mark_restricted(
        transitions.baseline_state,
        inputs.control_snapshot.record,
        inputs.proof,
        control_lease_lineage=(inputs.control_snapshot.record,),
        updated_at="2026-07-21T10:00:04Z",
    )
    stored_restricted = inputs.store.compare_and_swap_state(
        generation_state_sha256(transitions.baseline_state),
        restricted,
        fence=None,
        permit=None,
        proof=inputs.proof,
    )
    safety = mark_safety_unproven(
        restricted,
        inputs.control_snapshot.record,
        inputs.proof,
        control_lease_lineage=(inputs.control_snapshot.record,),
        updated_at="2026-07-21T10:00:05Z",
    )
    stored_safety = inputs.store.compare_and_swap_state(
        stored_restricted.sha256,
        safety,
        fence=None,
        permit=None,
        proof=inputs.proof,
    )
    forged_exit = dataclasses.replace(
        safety,
        state_version=safety.state_version + 1,
        mode=GenerationMode.RESTRICTED,
        updated_at="2026-07-21T10:00:06Z",
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            stored_safety.sha256,
            forged_exit,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == safety


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test公开状态只能用已持久精确permit从维护态A原子推进到B(
    tmp_path,
) -> None:
    """state CAS 不得允许无 permit 或绑定其他 B 的调用提前打开维护门禁。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    stored_permit = inputs.store.write_serving_permit_once(
        publication.permit,
        target_state=publication.public_state,
        proof=inputs.proof,
    )

    published = inputs.store.compare_and_swap_state(
        generation_state_sha256(publication.staged_state),
        publication.public_state,
        fence=None,
        permit=stored_permit.value,
        proof=inputs.proof,
    )

    assert published.state == publication.public_state
    assert inputs.store.load_state() == published


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testCAS拒绝伪造维护态绕过纯状态转换(
    tmp_path,
) -> None:
    """任意 maintenance 状态也不能成为绕过 commit_serving 的中转旁路。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    forged = dataclasses.replace(
        publication.staged_state,
        state_version=publication.staged_state.state_version + 1,
        mode=GenerationMode.SWITCHING,
        desired_generation_id="2" * 64,
        rollback_generation_id=publication.staged_state.serving_generation_id,
        updated_at="2026-07-21T10:00:07Z",
    )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            generation_state_sha256(publication.staged_state),
            forged,
            fence=None,
            permit=None,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == publication.staged_state
