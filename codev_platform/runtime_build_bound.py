"""薄 release facade 到 root-fd worker 的受限调度，不承载目录 I/O。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_metadata_io import decode_typed_bytes
from codev_platform.core.runtime_models import (
    ReleaseMetadata,
    canonical_json_bytes,
    require_sha256,
)
from codev_platform.runtime_bound_worker import RuntimeBoundWorkerError, run_bound_operation
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_object_lock_capability import RuntimeObjectLockCapabilityError
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError
from codev_platform.runtime_storage import id_lock_at, object_lock


_PREPARED_FIELDS = frozenset({"base_metadata_sha256", "release_id"})
_REQUEST_FIELDS = frozenset({"base_id", "candidate_file", "runtime_root", "wheel"})
_BASE_ID_FIELDS = frozenset({"base_id"})


@dataclass(frozen=True, slots=True)
class _PreparedRelease:
    """仅在父进程同一根租约内使用的 release 身份预计算结果。"""

    release_id: str
    base_metadata_sha256: str


def stage_bound_release(
    runtime_root: Path,
    wheel: Path,
    candidate_file: Path,
    base_id: str,
) -> ReleaseMetadata:
    """先锁共享基座、再锁目标 release，并把全部根 I/O 委托给静态 worker。"""
    if not isinstance(runtime_root, Path):
        raise RuntimeBuildError("薄 release 运行时根路径无效")
    resolved_base_id = _require_sha256(base_id, "基座 ID")
    request = _request(runtime_root, wheel, candidate_file, resolved_base_id)
    try:
        with object_lock(runtime_root, "base", resolved_base_id, shared=True) as base_lock:
            with base_lock.hold_active(
                kind="base",
                object_id=resolved_base_id,
                exclusive=False,
            ) as root:
                prepared = _decode_prepared(
                    _run_worker_and_verify(root, "release-prepare", request),
                )
                with id_lock_at(
                    root,
                    "release",
                    prepared.release_id,
                    shared=False,
                ) as release_lock:
                    with release_lock.hold_active(
                        kind="release",
                        object_id=prepared.release_id,
                        exclusive=True,
                    ) as release_root:
                        if release_root is not root:
                            raise RuntimeBuildError("薄 release 对象锁不属于同一根租约")
                        result = _run_worker_and_verify(
                            release_root,
                            "release-stage",
                            {
                                **request,
                                "base_metadata_sha256": prepared.base_metadata_sha256,
                                "release_id": prepared.release_id,
                            },
                        )
    except RuntimeObjectLockCapabilityError as error:
        raise RuntimeBuildError("薄 release 对象锁 capability 已失效") from error
    return _decode_metadata(result)


def verify_bound_release(runtime_root: Path, release_id: str) -> ReleaseMetadata:
    """在同一根租约内先锁 release，再锁其持有的基座并委托 worker 复验。"""
    if not isinstance(runtime_root, Path):
        raise RuntimeBuildError("薄 release 复验运行时根路径无效")
    resolved_release_id = _require_sha256(release_id, "release ID")
    request = {"release_id": resolved_release_id, "runtime_root": str(runtime_root)}
    try:
        with object_lock(runtime_root, "release", resolved_release_id, shared=True) as release_lock:
            with release_lock.hold_active(
                kind="release",
                object_id=resolved_release_id,
                exclusive=False,
            ) as root:
                base_id = _decode_base_id(
                    _run_worker_and_verify(
                        root,
                        "release-read-base",
                        request,
                        timeout_sec=120.0,
                    ),
                )
                with id_lock_at(root, "base", base_id, shared=True) as base_lock:
                    with base_lock.hold_active(
                        kind="base",
                        object_id=base_id,
                        exclusive=False,
                    ) as base_root:
                        if base_root is not root:
                            raise RuntimeBuildError("薄 release 复验对象锁不属于同一根租约")
                        result = _run_worker_and_verify(
                            base_root,
                            "release-verify",
                            {**request, "base_id": base_id},
                            timeout_sec=120.0,
                        )
    except RuntimeObjectLockCapabilityError as error:
        raise RuntimeBuildError("薄 release 复验对象锁 capability 已失效") from error
    return _decode_metadata(result)


def _run_worker_and_verify(
    root: BoundRuntimeRoot,
    operation: str,
    payload: dict[str, object],
    *,
    timeout_sec: float = 1800.0,
) -> dict[str, object]:
    worker_error: RuntimeBoundWorkerError | None = None
    result: dict[str, object] | None = None
    try:
        result = run_bound_operation(root, operation, payload, timeout_sec=timeout_sec)
    except RuntimeBoundWorkerError as error:
        worker_error = error
    try:
        root.verify_visible()
    except RuntimeRootBindingError as error:
        raise RuntimeBuildError("薄 release worker 执行期间运行时根目录已漂移") from error
    if worker_error is not None:
        raise RuntimeBuildError("薄 release 受管根 worker 执行失败") from worker_error
    if result is None:
        raise RuntimeBuildError("薄 release 受管根 worker 未返回结果")
    return result


def _request(
    runtime_root: Path,
    wheel: Path,
    candidate_file: Path,
    base_id: str,
) -> dict[str, object]:
    return {
        "base_id": base_id,
        "candidate_file": str(_absolute_path(candidate_file, "候选元数据")),
        "runtime_root": str(_absolute_path(runtime_root, "运行时根")),
        "wheel": str(_absolute_path(wheel, "候选 wheel")),
    }


def _absolute_path(value: Path, label: str) -> Path:
    if not isinstance(value, Path):
        raise RuntimeBuildError(f"薄 release {label}路径无效")
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise RuntimeBuildError(f"薄 release {label}路径必须规范绝对")
    return path


def _decode_prepared(value: dict[str, object]) -> _PreparedRelease:
    if set(value) != _PREPARED_FIELDS:
        raise RuntimeBuildError("薄 release worker 预计算结果字段无效")
    return _PreparedRelease(
        release_id=_require_sha256(value["release_id"], "release ID"),
        base_metadata_sha256=_require_sha256(
            value["base_metadata_sha256"],
            "基座元数据摘要",
        ),
    )


def _decode_metadata(value: dict[str, object]) -> ReleaseMetadata:
    try:
        return decode_typed_bytes(canonical_json_bytes(value), ReleaseMetadata)
    except ValueError as error:
        raise RuntimeBuildError("薄 release worker 返回元数据无效") from error


def _decode_base_id(value: dict[str, object]) -> str:
    if set(value) != _BASE_ID_FIELDS:
        raise RuntimeBuildError("薄 release worker 基座身份结果无效")
    return _require_sha256(value["base_id"], "基座 ID")


def _require_sha256(value: object, label: str) -> str:
    try:
        return require_sha256(value, field=label)
    except ValueError as error:
        raise RuntimeBuildError(f"薄 release {label}无效") from error


__all__ = ["stage_bound_release", "verify_bound_release"]
