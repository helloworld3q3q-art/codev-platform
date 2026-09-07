"""root-fd child 内部执行的基座构建操作；父进程始终持有对象 flock。"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

from codev_platform._runtime_base_errors import RuntimeBaseIntegrityError
from codev_platform.core.runtime_metadata_io import decode_typed_bytes
from codev_platform.core.runtime_models import (
    RequirementsLockInfo,
    RuntimeAbi,
    canonical_json_bytes,
    require_sha256,
    sha256_file,
)
from codev_platform.runtime_dependency_contract import (
    DistributionPin,
    LockedWheelArtifact,
    RequirementsLockContract,
)
from codev_platform.runtime_bound_worker_object_access import (
    seal_object_access_from_cwd,
    verify_object_access_from_cwd,
)
from codev_platform.runtime_cwd_layout import (
    RuntimeCwdLayoutError,
    verify_runtime_layout_from_cwd,
)
from codev_platform.runtime_object_access import RuntimeObjectAccessError
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBinding


_FIELDS = frozenset(
    {"abi", "base_id", "contract", "lock_path", "runtime_root", "wheelhouse"},
)
_VERIFY_FIELDS = frozenset({"base_id", "deep", "runtime_root"})


def build_base(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在已 fchdir 的旧根 inode 内完成基座构建和必要隔离。"""
    from codev_platform import runtime_base

    runtime_root, lock_path, contract, abi, base_id, wheelhouse = _decode_request(payload)
    if sha256_file(lock_path) != contract.info.requirements_sha256:
        raise RuntimeError("基座 worker 依赖锁摘要已漂移")
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor) as bound_root:
        root = Path(".")
        _verify_runtime_layout()
        worker_ports = _worker_ports(runtime_base._default_ports(), bound_root)
        metadata = runtime_base._build_locked(
            root,
            object(),
            lock_path,
            contract,
            abi,
            base_id,
            worker_ports,
            wheelhouse=wheelhouse,
        )
        return json.loads(canonical_json_bytes(metadata))


def verify_base(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在父进程持有共享基座锁时完成静态或深度复验。"""
    from codev_platform import runtime_base

    runtime_root, base_id, deep = _decode_verify_request(payload)
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor) as bound_root:
        root = Path(".")
        _verify_runtime_layout()
        ports = _worker_ports(runtime_base._default_ports(), bound_root)
        metadata = runtime_base._verify_locked(root, base_id, ports)
        if deep:
            _verify_deep_locked(runtime_base, root, base_id, metadata, ports)
            metadata = runtime_base._verify_locked(root, base_id, ports)
        return json.loads(canonical_json_bytes(metadata))


def verify_base_locked_from_cwd(
    base_id: str,
    bound_root: BoundRuntimeRoot,
):
    """复用既有 root binding，在 cwd 相对路径中复验已锁定基座。"""
    from codev_platform import runtime_base

    _verify_runtime_layout()
    ports = _worker_ports(runtime_base._default_ports(), bound_root)
    return runtime_base._verify_locked(Path("."), base_id, ports)


def _worker_ports(ports: object, bound_root: BoundRuntimeRoot):
    return replace(
        ports,
        isolate_incomplete=lambda _lock, kind, object_id: _isolate_under_parent_lock(
            bound_root,
            kind,
            object_id,
            completed_failure_stage=None,
        ),
        isolate_corrupt=lambda _lock, kind, object_id: _isolate_under_parent_lock(
            bound_root,
            kind,
            object_id,
            completed_failure_stage="completed_corrupt",
        ),
        seal_object_access=seal_object_access_from_cwd,
        verify_object_access=_verify_base_object_access,
        verify_execution_trust=_verify_base_execution_trust_from_cwd,
    )


def _verify_runtime_layout() -> None:
    """将 cwd 直接布局失败收敛为基座领域完整性错误。"""
    try:
        verify_runtime_layout_from_cwd()
    except RuntimeCwdLayoutError as error:
        raise RuntimeBaseIntegrityError(str(error)) from None


def _verify_base_object_access(root: Path) -> None:
    """把通用对象访问失败收敛为基座内核可隔离的领域错误。"""
    try:
        verify_object_access_from_cwd(root)
    except RuntimeObjectAccessError:
        raise RuntimeBaseIntegrityError("基座对象访问模式无法复验") from None


def _verify_base_execution_trust_from_cwd(
    _runtime_root: Path,
    base_dir: Path,
    python: Path,
    purelib: Path,
    bin_dir: Path,
) -> None:
    """基座 worker 只在继承 cwd 中证明即将执行的解释器与目录。"""
    from codev_platform.runtime_execution_trust import (
        RuntimeExecutionTrustError,
        verify_execution_trust_from_cwd,
    )

    try:
        verify_execution_trust_from_cwd(
            base_dir,
            ((python, True), (purelib, False), (bin_dir, False)),
        )
    except RuntimeExecutionTrustError as error:
        raise RuntimeBaseIntegrityError(str(error)) from None


def _verify_deep_locked(
    runtime_base: object,
    root: Path,
    base_id: str,
    metadata: object,
    ports: object,
) -> None:
    base_dir = root / "bases" / base_id
    python = runtime_base._managed_file(
        base_dir,
        metadata.python_relative,
        "基座解释器",
        allow_symlink=True,
    )
    freeze = runtime_base._run_deep_probes(ports, python)
    if hashlib.sha256(freeze).hexdigest() != metadata.freeze_sha256:
        raise RuntimeError("基座 worker 深度复验 freeze 摘要漂移")


def _decode_request(
    payload: dict[str, object],
) -> tuple[Path, Path, RequirementsLockContract, RuntimeAbi, str, Path | None]:
    if set(payload) != _FIELDS:
        raise ValueError("基座 worker 请求字段无效")
    runtime_root = _absolute_external_path(payload["runtime_root"], "运行时根")
    lock_path = _absolute_external_path(payload["lock_path"], "依赖锁")
    wheelhouse_value = payload["wheelhouse"]
    wheelhouse = (
        None
        if wheelhouse_value is None
        else _absolute_external_path(wheelhouse_value, "wheelhouse")
    )
    abi = decode_typed_bytes(canonical_json_bytes(payload["abi"]), RuntimeAbi)
    base_id = _require_base_id(payload["base_id"])
    return (
        runtime_root,
        lock_path,
        _decode_contract(payload["contract"]),
        abi,
        base_id,
        wheelhouse,
    )


def _decode_verify_request(payload: dict[str, object]) -> tuple[Path, str, bool]:
    if set(payload) != _VERIFY_FIELDS:
        raise ValueError("基座 worker 复验请求字段无效")
    base_id = payload["base_id"]
    deep = payload["deep"]
    if type(deep) is not bool:
        raise ValueError("基座 worker 复验请求类型无效")
    return (
        _absolute_external_path(payload["runtime_root"], "运行时根"),
        _require_base_id(base_id),
        deep,
    )


def _require_base_id(value: object) -> str:
    try:
        return require_sha256(value, field="base_id")
    except ValueError as error:
        raise ValueError("基座 worker base_id 无效") from error


def _absolute_external_path(value: object, label: str) -> Path:
    if type(value) is not str:
        raise ValueError(f"基座 worker {label}路径无效")
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise ValueError(f"基座 worker {label}路径必须规范绝对")
    return path


def _decode_contract(value: object) -> RequirementsLockContract:
    if type(value) is not dict or set(value) != {"artifacts", "info", "pins"}:
        raise ValueError("基座 worker 依赖契约无效")
    info_value = value["info"]
    pins_value = value["pins"]
    artifacts_value = value["artifacts"]
    if type(info_value) is not dict or set(info_value) != {
        "approved_index_url",
        "artifact_manifest_sha256",
        "cuda_tags",
        "pin_count",
        "requirements_sha256",
    }:
        raise ValueError("基座 worker 依赖身份无效")
    if type(pins_value) is not list or type(artifacts_value) is not list:
        raise ValueError("基座 worker 依赖集合无效")
    cuda_tags = info_value["cuda_tags"]
    if type(cuda_tags) is not list or any(type(tag) is not str for tag in cuda_tags):
        raise ValueError("基座 worker CUDA 标签无效")
    info = RequirementsLockInfo(
        requirements_sha256=info_value["requirements_sha256"],
        approved_index_url=info_value["approved_index_url"],
        pin_count=info_value["pin_count"],
        artifact_manifest_sha256=info_value["artifact_manifest_sha256"],
        cuda_tags=frozenset(cuda_tags),
    )
    pins = tuple(
        DistributionPin(name=item["name"], version=item["version"])
        for item in _strict_items(pins_value, {"name", "version"})
    )
    artifacts = tuple(
        LockedWheelArtifact(
            package=item["package"],
            filename=item["filename"],
            sha256=item["sha256"],
        )
        for item in _strict_items(artifacts_value, {"filename", "package", "sha256"})
    )
    return RequirementsLockContract(info=info, pins=pins, artifacts=artifacts)


def _strict_items(values: list[object], fields: set[str]) -> tuple[dict[str, str], ...]:
    items: list[dict[str, str]] = []
    for value in values:
        if type(value) is not dict or set(value) != fields:
            raise ValueError("基座 worker 依赖条目无效")
        if any(type(item) is not str for item in value.values()):
            raise ValueError("基座 worker 依赖条目无效")
        items.append(dict(value))
    return tuple(items)


def _isolate_under_parent_lock(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> Path | None:
    """仅由静态 worker 调用；父进程在整个 child 生命周期持有对应排他 flock。"""
    from codev_platform.runtime_isolation import _isolate_bound_root

    return _isolate_bound_root(
        root,
        kind,
        object_id,
        completed_failure_stage=completed_failure_stage,
    )
