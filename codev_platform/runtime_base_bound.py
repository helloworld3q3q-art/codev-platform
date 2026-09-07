"""基座 facade 到 root-fd worker 的受限调度，不承载任何目录 I/O。"""

from __future__ import annotations

import json
from pathlib import Path

from codev_platform._runtime_base_errors import RuntimeBaseError
from codev_platform.core.runtime_metadata_io import decode_typed_bytes
from codev_platform.core.runtime_models import BaseMetadata, RuntimeAbi, canonical_json_bytes
from codev_platform.runtime_bound_worker import RuntimeBoundWorkerError, run_bound_operation
from codev_platform.runtime_dependency_contract import RequirementsLockContract
from codev_platform.runtime_object_lock_capability import (
    BoundRuntimeObjectLock,
    RuntimeObjectLockCapabilityError,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError


def run_bound_build(
    lock: object,
    *,
    runtime_root: Path,
    lock_path: Path,
    contract: RequirementsLockContract,
    abi: RuntimeAbi,
    base_id: str,
    wheelhouse: Path | None,
) -> BaseMetadata:
    """在父进程持有排他对象锁期间，委托 worker 构建最终基座。"""
    if not isinstance(runtime_root, Path):
        raise RuntimeBaseError("基座构建运行时根路径无效")
    if type(lock) is not BoundRuntimeObjectLock:
        raise RuntimeBaseError("基座构建对象锁 capability 无效")
    try:
        with lock.hold_active(kind="base", object_id=base_id, exclusive=True) as root:
            result = _run_worker_and_verify(
                root,
                "base-build",
                _build_request(
                    runtime_root,
                    lock_path,
                    contract,
                    abi,
                    base_id,
                    wheelhouse,
                ),
                timeout_sec=1800.0,
                phase="基座构建",
            )
            return decode_typed_bytes(canonical_json_bytes(result), BaseMetadata)
    except RuntimeRootBindingError as error:
        raise RuntimeBaseError("基座构建期间运行时根目录已漂移") from error
    except RuntimeObjectLockCapabilityError as error:
        raise RuntimeBaseError("基座构建对象锁 capability 已失效") from error
    except ValueError as error:
        raise RuntimeBaseError("基座受管根 worker 返回元数据无效") from error


def run_bound_verify(
    lock: object,
    *,
    runtime_root: Path,
    base_id: str,
    deep: bool,
) -> BaseMetadata:
    """在父进程持有共享对象锁期间，委托 worker 复验已完成基座。"""
    if not isinstance(runtime_root, Path):
        raise RuntimeBaseError("基座复验运行时根路径无效")
    if type(lock) is not BoundRuntimeObjectLock:
        raise RuntimeBaseError("基座复验对象锁 capability 无效")
    if type(deep) is not bool:
        raise RuntimeBaseError("基座复验深度参数无效")
    try:
        with lock.hold_active(kind="base", object_id=base_id, exclusive=False) as root:
            result = _run_worker_and_verify(
                root,
                "base-verify",
                {"base_id": base_id, "deep": deep, "runtime_root": str(runtime_root)},
                timeout_sec=900.0 if deep else 120.0,
                phase="基座复验",
            )
            return decode_typed_bytes(canonical_json_bytes(result), BaseMetadata)
    except RuntimeRootBindingError as error:
        raise RuntimeBaseError("基座复验期间运行时根目录已漂移") from error
    except RuntimeObjectLockCapabilityError as error:
        raise RuntimeBaseError("基座复验对象锁 capability 已失效") from error
    except ValueError as error:
        raise RuntimeBaseError("基座受管根 worker 返回元数据无效") from error


def _run_worker_and_verify(
    root: BoundRuntimeRoot,
    operation: str,
    payload: dict[str, object],
    *,
    timeout_sec: float,
    phase: str,
) -> dict[str, object]:
    """无论 worker 成败，均先复验命名根，令漂移错误优先。"""
    worker_error: RuntimeBoundWorkerError | None = None
    result: dict[str, object] | None = None
    try:
        result = run_bound_operation(root, operation, payload, timeout_sec=timeout_sec)
    except RuntimeBoundWorkerError as error:
        worker_error = error
    try:
        root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeBaseError(f"{phase}期间运行时根目录已漂移") from error
    if worker_error is not None:
        raise RuntimeBaseError(f"{phase}受管根 worker 执行失败") from worker_error
    if result is None:
        raise RuntimeBaseError(f"{phase}受管根 worker 未返回结果")
    return result


def _build_request(
    runtime_root: Path,
    lock_path: Path,
    contract: RequirementsLockContract,
    abi: RuntimeAbi,
    base_id: str,
    wheelhouse: Path | None,
) -> dict[str, object]:
    return {
        "abi": json.loads(canonical_json_bytes(abi)),
        "base_id": base_id,
        "contract": _contract_value(contract),
        "lock_path": str(lock_path),
        "runtime_root": str(runtime_root),
        "wheelhouse": None if wheelhouse is None else str(wheelhouse),
    }


def _contract_value(contract: RequirementsLockContract) -> dict[str, object]:
    return {
        "artifacts": [
            {"filename": item.filename, "package": item.package, "sha256": item.sha256}
            for item in contract.artifacts
        ],
        "info": {
            "approved_index_url": contract.info.approved_index_url,
            "artifact_manifest_sha256": contract.info.artifact_manifest_sha256,
            "cuda_tags": sorted(contract.info.cuda_tags),
            "pin_count": contract.info.pin_count,
            "requirements_sha256": contract.info.requirements_sha256,
        },
        "pins": [{"name": pin.name, "version": pin.version} for pin in contract.pins],
    }


__all__ = ["run_bound_build", "run_bound_verify"]
