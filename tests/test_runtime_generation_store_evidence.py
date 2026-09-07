"""generation store 持久 evidence 叶子的严格读取对抗测试。"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_fencing import encode_serving_fence_record
from codev_platform.runtime_generation_state import generation_state_sha256
from codev_platform.runtime_generation_store import RuntimeGenerationStoreError
from codev_platform.runtime_managed_file import create_managed_bytes_exclusive_at
from codev_platform.runtime_serving_permit import (
    encode_serving_permit,
    serving_permit_sha256,
)
from codev_platform.runtime_storage import (
    acceptance_record_path,
    serving_fence_record_path,
    serving_permit_record_path,
    serving_permit_stage_path,
)
from codev_platform.runtime_store_protocols import private_managed_file_policy
from tests.runtime_generation_store_support import (
    build_generation_store_input,
    build_serving_publication_input,
)


_POSIX = os.name == "posix"


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testacceptance拒绝截断的已持久fence且不创建acceptance(
    tmp_path: Path,
) -> None:
    """acceptance 必须从完整规范 fence 读取，不能继承调用方对象。"""
    inputs = build_generation_store_input(tmp_path)
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    serving_fence_record_path(
        inputs.root,
        inputs.fence.accepted_attempt_id,
        canonical_sha256(inputs.fence),
    ).write_bytes(b"{")

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_acceptance_once(
            inputs.acceptance,
            fence=inputs.fence,
            proof=inputs.proof,
        )

    assert not acceptance_record_path(inputs.root, inputs.acceptance.attempt_id).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit拒绝符号链接acceptance且不创建stage锚点(
    tmp_path: Path,
) -> None:
    """permit 写入不能跟随 acceptance 叶子离开受管 runtime 根。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    path = acceptance_record_path(inputs.root, inputs.acceptance.attempt_id)
    outside = tmp_path / "outside-acceptance.json"
    outside.write_bytes(b"outside")
    path.unlink()
    path.symlink_to(outside)

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_serving_permit_once(
            publication.permit,
            target_state=publication.public_state,
            proof=inputs.proof,
        )

    assert outside.read_bytes() == b"outside"
    assert not serving_permit_stage_path(
        inputs.root,
        publication.permit.attempt_id,
        generation_state_sha256(publication.staged_state),
    ).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test公开CAS拒绝截断内容permit并保持维护态A(
    tmp_path: Path,
) -> None:
    """stage 锚点与内容寻址 permit 必须同时严格存在且逐字节可验证。"""
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
    serving_permit_record_path(
        inputs.root,
        permit.value.attempt_id,
        serving_permit_sha256(permit.value),
    ).write_bytes(b"{")

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
def test公开CAS拒绝非普通permit_stage叶子(
    tmp_path: Path,
) -> None:
    """目录等非普通 stage 叶子不得被错误解释为缺失或有效 permit。"""
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
    stage_path = serving_permit_stage_path(
        inputs.root,
        permit.value.attempt_id,
        generation_state_sha256(publication.staged_state),
    )
    stage_path.unlink()
    stage_path.mkdir()

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
def test同值permit重试能在仅留下stage锚点后补齐内容记录(
    tmp_path: Path,
) -> None:
    """stage 先落盘后的崩溃只能允许同值恢复，不能放宽为新 permit。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    payload = encode_serving_permit(publication.permit)
    stage_path = serving_permit_stage_path(
        inputs.root,
        publication.permit.attempt_id,
        generation_state_sha256(publication.staged_state),
    )
    policy = private_managed_file_policy(inputs.policy.owner_uid, 16_384)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            stage_path,
            payload,
            root=root,
            policy=policy,
        )

    stored = inputs.store.write_serving_permit_once(
        publication.permit,
        target_state=publication.public_state,
        proof=inputs.proof,
    )

    assert stored.value == publication.permit
    assert serving_permit_record_path(
        inputs.root,
        stored.value.attempt_id,
        serving_permit_sha256(stored.value),
    ).exists()


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def test围栏内容寻址路径被异值预占时绝不覆盖(
    tmp_path: Path,
) -> None:
    """内容寻址路径只接受其自身规范 bytes，不能覆盖异值预占叶子。"""
    inputs = build_generation_store_input(tmp_path)
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    drifted = replace(inputs.fence, issued_at="2026-07-21T10:00:06Z")
    path = serving_fence_record_path(
        inputs.root,
        inputs.fence.accepted_attempt_id,
        canonical_sha256(inputs.fence),
    )
    drifted_payload = encode_serving_fence_record(drifted)
    policy = private_managed_file_policy(inputs.policy.owner_uid, 16_384)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            path,
            drifted_payload,
            root=root,
            policy=policy,
        )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)

    assert path.read_bytes() == drifted_payload


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit内容寻址路径被异值预占时绝不覆盖(
    tmp_path: Path,
) -> None:
    """stage 可先安全落盘，但异值内容 permit 必须保持拒绝且不被覆盖。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    drifted = replace(publication.permit, issued_at="2026-07-21T10:00:09Z")
    path = serving_permit_record_path(
        inputs.root,
        publication.permit.attempt_id,
        serving_permit_sha256(publication.permit),
    )
    drifted_payload = encode_serving_permit(drifted)
    policy = private_managed_file_policy(inputs.policy.owner_uid, 16_384)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            path,
            drifted_payload,
            root=root,
            policy=policy,
        )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_serving_permit_once(
            publication.permit,
            target_state=publication.public_state,
            proof=inputs.proof,
        )

    assert path.read_bytes() == drifted_payload
    stage_path = serving_permit_stage_path(
        inputs.root,
        publication.permit.attempt_id,
        generation_state_sha256(publication.staged_state),
    )
    assert stage_path.read_bytes() == encode_serving_permit(publication.permit)
    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.compare_and_swap_state(
            generation_state_sha256(publication.staged_state),
            publication.public_state,
            fence=None,
            permit=publication.permit,
            proof=inputs.proof,
        )

    assert inputs.store.load_state().state == publication.staged_state


@pytest.mark.skipif(not _POSIX, reason="受管文件与部署锁仅在 WSL/Linux 验证")
def testpermit_stage被异值预占时不创建内容寻址记录(
    tmp_path: Path,
) -> None:
    """同一维护态 A 只能保留首个 stage，异值不能越过它写入内容记录。"""
    publication = build_serving_publication_input(tmp_path)
    inputs = publication.base
    inputs.store.write_generation_once(inputs.generation, proof=inputs.proof)
    inputs.store.write_serving_fence_once(inputs.fence, proof=inputs.proof)
    inputs.store.write_acceptance_once(
        inputs.acceptance,
        fence=inputs.fence,
        proof=inputs.proof,
    )
    drifted = replace(publication.permit, issued_at="2026-07-21T10:00:09Z")
    stage_path = serving_permit_stage_path(
        inputs.root,
        publication.permit.attempt_id,
        generation_state_sha256(publication.staged_state),
    )
    drifted_payload = encode_serving_permit(drifted)
    policy = private_managed_file_policy(inputs.policy.owner_uid, 16_384)
    with inputs.policy.root_binding.bind() as root:
        create_managed_bytes_exclusive_at(
            stage_path,
            drifted_payload,
            root=root,
            policy=policy,
        )

    with pytest.raises(RuntimeGenerationStoreError):
        inputs.store.write_serving_permit_once(
            publication.permit,
            target_state=publication.public_state,
            proof=inputs.proof,
        )

    assert stage_path.read_bytes() == drifted_payload
    assert not serving_permit_record_path(
        inputs.root,
        publication.permit.attempt_id,
        serving_permit_sha256(publication.permit),
    ).exists()
