"""回滚包 store 的严格记录读取、规范 bytes 与持久绑定校验。"""

from __future__ import annotations

import hashlib
from typing import TypeVar

from codev_platform._runtime_store_public_input import StorePublicInputValidator
from codev_platform.runtime_attempt_contract import (
    AttemptReservation,
    DeploymentAttempt,
    decode_deployment_attempt,
    encode_deployment_attempt,
    verify_frozen_attempt,
)
from codev_platform.runtime_generation_contract import (
    RuntimeGeneration,
    decode_runtime_generation,
    encode_runtime_generation,
)
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    read_managed_bytes_at,
    read_optional_managed_bytes_at,
)
from codev_platform.runtime_rollback_contract import (
    RollbackBundle,
    decode_rollback_bundle,
    encode_rollback_bundle,
    configuration_manifest_sha256,
    systemd_receipt_manifest_sha256,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot
from codev_platform.runtime_storage import (
    attempt_record_path,
    generation_record_path,
    rollback_bundle_path,
    rollback_bundle_pending_path,
)
from codev_platform.runtime_store_protocols import StoredSnapshot


_Value = TypeVar("_Value")


class RuntimeRollbackStoreError(RuntimeError):
    """回滚包或其受保护载荷无法安全持久化或读取。"""


class RollbackBundleConflictError(RuntimeRollbackStoreError):
    """同一 attempt 的不可变回滚包已存在但完整 bytes 不同。"""


_INPUT = StorePublicInputValidator(RuntimeRollbackStoreError)


def normalize_public_bundle(value: object) -> StoredSnapshot[RollbackBundle]:
    """在进入控制 gate 前重解码不受信 bundle 并固定唯一规范 bytes。"""
    payload = _INPUT.encode(value, encode_rollback_bundle, label="rollback bundle")
    try:
        bundle = decode_rollback_bundle(payload)
    except ValueError as error:
        raise RuntimeRollbackStoreError("rollback bundle 无法严格解码") from error
    canonical = encode_rollback_bundle(bundle)
    if canonical != payload:
        raise RuntimeRollbackStoreError("rollback bundle 不是规范编码")
    return snapshot(bundle, payload)


def load_rollback_bundle(
    root_path: object,
    attempt_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[RollbackBundle]:
    """严格读取 bundle，并复验路径 attempt 与规范 bytes。"""
    payload = read_managed_bytes_at(
        rollback_bundle_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    return _decode_rollback_bundle(payload, attempt_id)


def load_optional_rollback_bundle(
    root_path: object,
    attempt_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
    allow_missing_parent: bool = False,
) -> StoredSnapshot[RollbackBundle] | None:
    """安全探测最终 bundle；仅预探测可明确允许缺失父目录。"""
    if type(allow_missing_parent) is not bool:
        raise RuntimeRollbackStoreError("allow_missing_parent 必须是 bool")
    payload = read_optional_managed_bytes_at(
        rollback_bundle_path(root_path, attempt_id),
        root=root,
        policy=policy,
        allow_missing_parent=allow_missing_parent,
    )
    if payload is None:
        return None
    return _decode_rollback_bundle(payload, attempt_id)


def load_pending_rollback_bundle(
    root_path: object,
    attempt_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[RollbackBundle]:
    """严格读取两阶段封口遗留的同 attempt 暂存 bundle。"""
    payload = read_managed_bytes_at(
        rollback_bundle_pending_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    return _decode_rollback_bundle(payload, attempt_id)


def _decode_rollback_bundle(
    payload: bytes,
    attempt_id: str,
) -> StoredSnapshot[RollbackBundle]:
    """严格解码任一固定路径下的 bundle bytes。"""
    try:
        bundle = decode_rollback_bundle(payload)
    except ValueError as error:
        raise RuntimeRollbackStoreError("rollback bundle 无法严格解码") from error
    if bundle.attempt_id != attempt_id:
        raise RuntimeRollbackStoreError("rollback bundle 与路径 attempt 不一致")
    require_canonical("rollback bundle", encode_rollback_bundle(bundle), payload)
    return snapshot(bundle, payload)


def load_frozen_attempt(
    root_path: object,
    attempt_id: str,
    reservation: AttemptReservation,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[DeploymentAttempt]:
    """严格读取冻结 attempt，并复验其继承的 reservation。"""
    payload = read_managed_bytes_at(
        attempt_record_path(root_path, attempt_id),
        root=root,
        policy=policy,
    )
    try:
        attempt = decode_deployment_attempt(payload)
        verify_frozen_attempt(reservation, attempt)
    except ValueError as error:
        raise RuntimeRollbackStoreError("冻结 attempt 无法严格绑定") from error
    if attempt.attempt_id != attempt_id:
        raise RuntimeRollbackStoreError("冻结 attempt 与路径身份不一致")
    require_canonical("冻结 attempt", encode_deployment_attempt(attempt), payload)
    return snapshot(attempt, payload)


def load_baseline_generation(
    root_path: object,
    generation_id: str,
    *,
    root: BoundRuntimeRoot,
    policy: ManagedFilePolicy,
) -> StoredSnapshot[RuntimeGeneration]:
    """严格读取冻结 attempt 指向的基线 generation。"""
    payload = read_managed_bytes_at(
        generation_record_path(root_path, generation_id),
        root=root,
        policy=policy,
    )
    try:
        generation = decode_runtime_generation(payload)
    except ValueError as error:
        raise RuntimeRollbackStoreError("基线 generation 无法严格解码") from error
    if generation.generation_id != generation_id:
        raise RuntimeRollbackStoreError("基线 generation 与路径身份不一致")
    require_canonical("基线 generation", encode_runtime_generation(generation), payload)
    return snapshot(generation, payload)


def require_bundle_persistence_binding(
    bundle: RollbackBundle,
    attempt: DeploymentAttempt,
    baseline_generation: RuntimeGeneration,
) -> None:
    """只复验当前布局可持久取得的 bundle、attempt 与基线 generation 真值。"""
    if type(bundle) is not RollbackBundle:
        raise RuntimeRollbackStoreError("rollback bundle 类型无效")
    if type(attempt) is not DeploymentAttempt:
        raise RuntimeRollbackStoreError("冻结 attempt 类型无效")
    if type(baseline_generation) is not RuntimeGeneration:
        raise RuntimeRollbackStoreError("基线 generation 类型无效")
    if attempt.baseline_generation_id != baseline_generation.generation_id:
        raise RuntimeRollbackStoreError("冻结 attempt 与基线 generation 身份不一致")
    expected = (
        attempt.attempt_id,
        attempt.plan_sha256,
        attempt.baseline_observation_sha256,
        attempt.baseline_generation_id,
        baseline_generation.database_contract_sha256,
        baseline_generation.index_set_sha256,
    )
    actual = (
        bundle.attempt_id,
        bundle.plan_sha256,
        bundle.baseline_observation_sha256,
        bundle.original_serving_generation_id,
        bundle.database_compatibility_sha256,
        bundle.index_set_sha256,
    )
    if actual != expected:
        raise RuntimeRollbackStoreError("rollback bundle 与持久 attempt 或基线 generation 不一致")
    if (
        systemd_receipt_manifest_sha256(
            bundle.systemd_payloads,
            bundle.receipt_payloads,
        )
        != baseline_generation.systemd_bundle_sha256
    ):
        raise RuntimeRollbackStoreError("rollback bundle 的 systemd/receipt manifest 不一致")
    if (
        configuration_manifest_sha256(bundle.configuration_payloads)
        != baseline_generation.configuration_bundle_sha256
    ):
        raise RuntimeRollbackStoreError("rollback bundle 的 configuration manifest 不一致")


def require_same_bundle_bytes(
    expected: bytes,
    existing: StoredSnapshot[RollbackBundle],
) -> None:
    """不可变 bundle 重试必须与已持久的完整规范 bytes 完全相同。"""
    if encode_rollback_bundle(existing.value) != expected:
        raise RollbackBundleConflictError("rollback bundle 已存在且内容漂移")


def require_canonical(label: str, encoded: bytes, payload: bytes) -> None:
    """拒绝可解码但不等于唯一规范编码的持久 bytes。"""
    if encoded != payload:
        raise RuntimeRollbackStoreError(f"{label} 不是规范编码")


def snapshot(value: _Value, payload: bytes) -> StoredSnapshot[_Value]:
    """为已严格验证的规范 bytes 生成不可变快照。"""
    return StoredSnapshot(value=value, sha256=hashlib.sha256(payload).hexdigest())


__all__ = [
    "RollbackBundleConflictError",
    "RuntimeRollbackStoreError",
    "load_baseline_generation",
    "load_frozen_attempt",
    "load_optional_rollback_bundle",
    "load_pending_rollback_bundle",
    "load_rollback_bundle",
    "normalize_public_bundle",
    "require_bundle_persistence_binding",
    "require_same_bundle_bytes",
]
