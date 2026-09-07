"""generation store 的严格单记录读取、身份绑定与错误边界。"""

from __future__ import annotations

import hashlib
from typing import TypeVar

from codev_platform._runtime_contract_support import (
    RuntimeContractSupportError,
    require_sha256,
    require_strictly_later,
)
from codev_platform.core.runtime_models import canonical_sha256
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    DeploymentAttempt,
    decode_deployment_attempt,
    encode_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_fencing import (
    ControlLeaseRecord,
    ServingFenceRecord,
    decode_serving_fence_record,
    encode_serving_fence_record,
)
from codev_platform.runtime_generation_acceptance import (
    GenerationAcceptance,
    decode_generation_acceptance,
    encode_generation_acceptance,
)
from codev_platform.runtime_generation_acceptance_validation import (
    GenerationAcceptanceBindingError,
    verify_serving_fence_acceptance,
)
from codev_platform.runtime_generation_state import (
    GenerationState,
    decode_generation_state,
    encode_generation_state,
    prepare_serving_publication,
)
from codev_platform.runtime_generation_state_control import (
    GenerationStateControlBindingError,
    require_acceptance_control_lease_lineage,
    require_bound_control_lease,
)
from codev_platform.runtime_serving_permit import (
    ServingPermitRecord,
    decode_serving_permit,
    encode_serving_permit,
    serving_permit_sha256,
    verify_serving_permit,
)
from codev_platform.runtime_generation_contract import (
    RuntimeGeneration,
    decode_runtime_generation,
    encode_runtime_generation,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    read_managed_bytes_at,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import (
    attempt_record_path,
    acceptance_record_path,
    generation_record_path,
    generation_state_path,
    serving_fence_record_path,
    serving_permit_stage_path,
    serving_permit_record_path,
)
from codev_platform.runtime_store_protocols import StoredSnapshot


_Value = TypeVar("_Value")


class RuntimeGenerationStoreError(RuntimeError):
    """generation、fence、acceptance、permit 或 state 无法安全持久化。"""


class GenerationRecordConflictError(RuntimeGenerationStoreError):
    """不可变 generation 记录已存在但规范内容不一致。"""


class GenerationStateConflictError(RuntimeGenerationStoreError):
    """generation state 的 CAS 前置摘要或唯一公开后继不成立。"""


def load_frozen_attempt(
    root_path: object,
    attempt_id: str,
    reservation: AttemptReservation,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[DeploymentAttempt]:
    """严格读取冻结 attempt，并复验 reservation 与路径身份。"""
    payload = read_managed_bytes_at(
        attempt_record_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    try:
        attempt = decode_deployment_attempt(payload)
        verify_frozen_attempt(reservation, attempt)
    except ValueError as error:
        raise RuntimeGenerationStoreError("冻结 attempt 无法严格绑定") from error
    if attempt.attempt_id != attempt_id:
        raise RuntimeGenerationStoreError("冻结 attempt 与路径身份不一致")
    require_canonical("冻结 attempt", encode_deployment_attempt(attempt), payload)
    return snapshot(attempt, payload)


def load_generation(
    root_path: object,
    generation_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[RuntimeGeneration]:
    """严格读取 generation，并复验路径与 generation 身份投影。"""
    payload = read_managed_bytes_at(
        generation_record_path(root_path, generation_id),
        root=root,
        policy=policy,
    )
    try:
        generation = decode_runtime_generation(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("generation 无法严格解码") from error
    if generation.generation_id != generation_id:
        raise RuntimeGenerationStoreError("generation 与路径身份不一致")
    require_canonical("generation", encode_runtime_generation(generation), payload)
    return snapshot(generation, payload)


def load_serving_fence(
    root_path: object,
    attempt_id: str,
    record_sha256: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[ServingFenceRecord]:
    """严格读取内容寻址 fence，并复验 attempt、摘要与规范 bytes。"""
    payload = read_managed_bytes_at(
        serving_fence_record_path(root_path, attempt_id, record_sha256),
        root=root,
        policy=policy,
    )
    try:
        fence = decode_serving_fence_record(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("serving fence 无法严格解码") from error
    if fence.accepted_attempt_id != attempt_id:
        raise RuntimeGenerationStoreError("serving fence 与路径 attempt 不一致")
    if canonical_sha256(fence) != record_sha256:
        raise RuntimeGenerationStoreError("serving fence 与路径摘要不一致")
    require_canonical("serving fence", encode_serving_fence_record(fence), payload)
    return snapshot(fence, payload)


def load_acceptance(
    root_path: object,
    attempt_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[GenerationAcceptance]:
    """严格读取 attempt 唯一验收记录，并复验路径与规范 bytes。"""
    payload = read_managed_bytes_at(
        acceptance_record_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    try:
        acceptance = decode_generation_acceptance(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("acceptance 无法严格解码") from error
    if acceptance.attempt_id != attempt_id:
        raise RuntimeGenerationStoreError("acceptance 与路径 attempt 不一致")
    require_canonical(
        "acceptance",
        encode_generation_acceptance(acceptance),
        payload,
    )
    return snapshot(acceptance, payload)


def load_generation_state(
    root_path: object,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[GenerationState]:
    """严格读取唯一状态文件；缺失、截断和非规范 bytes 均闭锁。"""
    payload = read_managed_bytes_at(
        generation_state_path(root_path),
        root=root,
        policy=policy,
    )
    try:
        state = decode_generation_state(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("generation state 无法严格解码") from error
    require_canonical("generation state", encode_generation_state(state), payload)
    return snapshot(state, payload)


def load_serving_permit(
    root_path: object,
    attempt_id: str,
    record_sha256: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[ServingPermitRecord]:
    """严格读取内容寻址 permit，并复验 attempt、摘要与规范 bytes。"""
    payload = read_managed_bytes_at(
        serving_permit_record_path(root_path, attempt_id, record_sha256),
        root=root,
        policy=policy,
    )
    try:
        permit = decode_serving_permit(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("serving permit 无法严格解码") from error
    if permit.attempt_id != attempt_id:
        raise RuntimeGenerationStoreError("serving permit 与路径 attempt 不一致")
    if serving_permit_sha256(permit) != record_sha256:
        raise RuntimeGenerationStoreError("serving permit 与路径摘要不一致")
    require_canonical("serving permit", encode_serving_permit(permit), payload)
    return snapshot(permit, payload)


def load_staged_serving_permit(
    root_path: object,
    attempt_id: str,
    staged_state_sha256: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[ServingPermitRecord]:
    """严格读取按维护态 A 唯一寻址的 staged permit 锚点。"""
    payload = read_managed_bytes_at(
        serving_permit_stage_path(
            root_path,
            attempt_id,
            staged_state_sha256,
        ),
        root=root,
        policy=policy,
    )
    try:
        permit = decode_serving_permit(payload)
    except ValueError as error:
        raise RuntimeGenerationStoreError("staged serving permit 无法严格解码") from error
    if permit.attempt_id != attempt_id:
        raise RuntimeGenerationStoreError("staged serving permit 与路径 attempt 不一致")
    require_canonical("staged serving permit", encode_serving_permit(permit), payload)
    return snapshot(permit, payload)


def require_attempt_target_generation(
    attempt: DeploymentAttempt,
    generation: RuntimeGeneration,
) -> None:
    """只允许为当前冻结 attempt 的目标代际发布 immutable generation。"""
    if type(attempt) is not DeploymentAttempt:
        raise RuntimeGenerationStoreError("只接受严格冻结的 DeploymentAttempt")
    if type(generation) is not RuntimeGeneration:
        raise RuntimeGenerationStoreError("只接受 RuntimeGeneration")
    if attempt.target_generation_id != generation.generation_id:
        raise RuntimeGenerationStoreError("generation 不是当前 attempt 的目标代际")


def require_fence_attempt_generation(
    fence: ServingFenceRecord,
    attempt: DeploymentAttempt,
    generation: RuntimeGeneration,
) -> None:
    """fence 只能冻结当前 attempt 的已持久目标 generation。"""
    if type(fence) is not ServingFenceRecord:
        raise RuntimeGenerationStoreError("只接受 ServingFenceRecord")
    require_attempt_target_generation(attempt, generation)
    if (
        fence.accepted_attempt_id != attempt.attempt_id
        or fence.generation_id != generation.generation_id
    ):
        raise RuntimeGenerationStoreError("serving fence 未绑定当前 attempt 的目标 generation")


def require_acceptance_bindings(
    acceptance: GenerationAcceptance,
    fence: ServingFenceRecord,
    attempt: DeploymentAttempt,
    generation: RuntimeGeneration,
    current_lease: ControlLeaseRecord,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    """验证 acceptance 的持久 fence、attempt、generation 与 lease 审计锚点。"""
    if type(acceptance) is not GenerationAcceptance:
        raise RuntimeGenerationStoreError("只接受 GenerationAcceptance")
    require_fence_attempt_generation(fence, attempt, generation)
    try:
        verify_serving_fence_acceptance(fence, acceptance)
        require_strictly_later(
            acceptance.accepted_at,
            fence.issued_at,
            field="acceptance accepted_at",
            boundary_field="ServingFence issued_at",
        )
        require_acceptance_control_lease_lineage(
            acceptance,
            reservation_sha256=current_lease.reservation_sha256,
            current_lease=current_lease,
            control_lease_lineage=control_lease_lineage,
        )
    except (
        GenerationAcceptanceBindingError,
        GenerationStateControlBindingError,
        RuntimeContractSupportError,
    ) as error:
        raise RuntimeGenerationStoreError("acceptance 缺少当前 control 谱系证明") from error


def require_staged_state_current_control(
    state: GenerationState,
    current_lease: ControlLeaseRecord,
    proof: object,
    control_lease_lineage: tuple[ControlLeaseRecord, ...],
) -> None:
    """维护态 A 必须仍可由当前 scope 的 control capability 继续推进。"""
    if type(state) is not GenerationState:
        raise RuntimeGenerationStoreError("只接受 GenerationState")
    try:
        require_bound_control_lease(
            control_attempt_id=state.control_attempt_id,
            control_reservation_sha256=state.control_reservation_sha256,
            control_lease_record_sha256_audit=state.control_lease_record_sha256,
            control_lease_epoch=state.control_lease_epoch,
            lease_record=current_lease,
            lease=proof,
            control_lease_lineage=control_lease_lineage,
            state_updated_at=state.updated_at,
        )
    except GenerationStateControlBindingError as error:
        raise RuntimeGenerationStoreError("维护态 A 未绑定当前 control 谱系") from error


def require_permit_target_state(
    permit: ServingPermitRecord,
    current_state: GenerationState,
    target_state: GenerationState,
    acceptance: GenerationAcceptance,
    fence: ServingFenceRecord,
) -> None:
    """permit 只能绑定 A 确定性导出的公开 B，或该 B 的同值重试。"""
    if type(target_state) is not GenerationState:
        raise RuntimeGenerationStoreError("只接受 GenerationState target")
    try:
        if current_state != target_state:
            expected = prepare_serving_publication(
                current_state,
                current_state.acceptance_sha256,
                updated_at=target_state.updated_at,
            )
            if target_state != expected:
                raise RuntimeGenerationStoreError("target state 不是维护态 A 的唯一公开后继")
        verify_serving_permit(permit, target_state, acceptance, fence)
    except RuntimeGenerationStoreError:
        raise
    except ValueError as error:
        raise RuntimeGenerationStoreError("serving permit 未绑定公开状态 B") from error


def require_state_cas_precondition(
    expected_sha256: str,
    current_state: GenerationState,
    current_sha256: str,
    desired_state: GenerationState,
) -> None:
    """验证 CAS 的通用前置条件，不在此处放宽任何状态边。"""
    if type(current_state) is not GenerationState or type(desired_state) is not GenerationState:
        raise GenerationStateConflictError("generation state CAS 只接受严格 GenerationState")
    try:
        require_sha256(expected_sha256, field="expected_sha256")
        require_strictly_later(
            desired_state.updated_at,
            current_state.updated_at,
            field="desired state updated_at",
            boundary_field="current state updated_at",
        )
    except RuntimeContractSupportError as error:
        raise GenerationStateConflictError("generation state CAS 参数无效") from error
    if expected_sha256 != current_sha256:
        raise GenerationStateConflictError("generation state CAS 前置摘要已变化")
    if desired_state.state_version != current_state.state_version + 1:
        raise GenerationStateConflictError("generation state 必须单步提升版本")


def require_same_bytes(label: str, expected: bytes, actual: bytes) -> None:
    """不可变重试只接受完全相同的规范字节。"""
    if type(label) is not str or not label:
        raise RuntimeGenerationStoreError("不可变记录标签无效")
    if expected != actual:
        raise GenerationRecordConflictError(f"{label} 已存在且内容漂移")


def require_canonical(label: str, encoded: bytes, payload: bytes) -> None:
    """拒绝虽可解码但不等于唯一规范编码的持久 bytes。"""
    if encoded != payload:
        raise RuntimeGenerationStoreError(f"{label} 不是规范编码")


def snapshot(value: _Value, payload: bytes) -> StoredSnapshot[_Value]:
    """为严格验证的规范 bytes 生成统一不可变快照。"""
    return StoredSnapshot(
        value=value,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


__all__ = [
    "GenerationRecordConflictError",
    "GenerationStateConflictError",
    "RuntimeGenerationStoreError",
    "load_acceptance",
    "load_frozen_attempt",
    "load_generation",
    "load_generation_state",
    "load_serving_fence",
    "load_serving_permit",
    "load_staged_serving_permit",
    "require_attempt_target_generation",
    "require_acceptance_bindings",
    "require_canonical",
    "require_fence_attempt_generation",
    "require_permit_target_state",
    "require_same_bytes",
    "require_state_cas_precondition",
    "require_staged_state_current_control",
    "snapshot",
]
