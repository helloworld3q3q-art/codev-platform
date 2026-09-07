"""内容寻址依赖基座的构建与完整性验证。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    RequirementsLockInfo,
    RuntimeAbi,
    compute_base_id,
    current_abi,
    read_base_metadata,
    require_sha256,
    sha256_file,
    write_base_metadata_atomic,
)
from codev_platform._runtime_base_errors import RuntimeBaseError, RuntimeBaseIntegrityError
from codev_platform._runtime_base_files import (
    copy_lock_verified as _copy_lock_verified,
    ensure_directory as _ensure_directory,
    managed_directory as _managed_directory,
    managed_file as _managed_file,
    relative_path as _relative,
    require_venv_layout as _require_venv_layout,
    run_isolated as _run_isolated,
    write_stage as _write_stage,
)
from codev_platform.runtime_base_bound import (
    run_bound_build as _run_bound_build,
    run_bound_verify as _run_bound_verify,
)
from codev_platform.runtime_dependency_contract import (
    DistributionPin,
    RUNTIME_IMPORT_MODULES,
    RequirementsLockContract,
)
from codev_platform.runtime_base_environment import (
    VenvLayout as _VenvLayout,
    create_venv as _create_venv,
    run_python as _run_python,
)
from codev_platform.runtime_deadline import runtime_operation_timeout
from codev_platform.runtime_distribution_inventory import DistributionInventoryProof
from codev_platform.runtime_errors import RuntimeIdCollisionError


_LOCK_RELATIVE, _MARKER_NAME, _METADATA_NAME = (
    "requirements.lock",
    ".incomplete",
    "base.json",
)


@dataclass(frozen=True, slots=True)
class _BasePorts:
    validate_lock: Callable[[Path, Path], RequirementsLockContract]
    inspect_lock: Callable[[Path, str], RequirementsLockContract]
    current_abi: Callable[[], RuntimeAbi]
    id_lock: Callable[..., AbstractContextManager[object]]
    isolate_incomplete: Callable[[object, str, str], Path | None]
    isolate_corrupt: Callable[[object, str, str], Path | None]
    fsync_directory: Callable[[Path], None]
    seal_durable_tree: Callable[[Path], None]
    seal_object_access: Callable[[Path], None]
    verify_object_access: Callable[[Path], None]
    verify_inventory: Callable[
        [Path, tuple[DistributionPin, ...]],
        DistributionInventoryProof,
    ]
    verify_wheelhouse: Callable[[RequirementsLockContract, Path], Path]
    create_venv: Callable[[Path], _VenvLayout]
    run_python: Callable[[Path, tuple[str, ...]], bytes]
    verify_execution_trust: Callable[[Path, Path, Path, Path, Path], None]
    utc_now: Callable[[], str]


def _default_ports() -> _BasePorts:
    from codev_platform.runtime_durable_tree import seal_durable_tree
    from codev_platform.runtime_distribution_inventory import verify_distribution_inventory
    from codev_platform.runtime_lock import (
        inspect_requirements_lock_contract,
        validate_requirements_lock_contract,
    )
    from codev_platform.runtime_wheelhouse import verify_locked_wheelhouse
    from codev_platform.runtime_storage import (
        fsync_directory,
        isolate_corrupt_completed_locked_at,
        isolate_incomplete_locked_at,
        object_lock,
    )

    return _BasePorts(
        validate_lock=validate_requirements_lock_contract,
        inspect_lock=inspect_requirements_lock_contract,
        current_abi=current_abi,
        id_lock=object_lock,
        isolate_incomplete=isolate_incomplete_locked_at,
        isolate_corrupt=isolate_corrupt_completed_locked_at,
        fsync_directory=fsync_directory,
        seal_durable_tree=seal_durable_tree,
        seal_object_access=_seal_object_access,
        verify_object_access=_verify_object_access,
        verify_inventory=verify_distribution_inventory,
        verify_wheelhouse=verify_locked_wheelhouse,
        create_venv=_create_venv,
        run_python=_run_python,
        verify_execution_trust=_verify_execution_trust,
        utc_now=_utc_now,
    )


@runtime_operation_timeout(1800.0)
def build_base(
    root: Path,
    lock: Path,
    approved_source: Path,
    wheelhouse: Path | None = None,
) -> BaseMetadata:
    """在最终内容寻址目录中构建一个依赖基座。"""
    _require_privileged_builder()
    ports = _default_ports()
    lock_path = Path(lock).expanduser().absolute()
    approved_path = Path(approved_source).expanduser().absolute()
    contract = ports.validate_lock(lock_path, approved_path)
    if not isinstance(contract, RequirementsLockContract):
        raise ValueError("依赖锁验证结果无效")
    info = contract.info
    if sha256_file(lock_path) != info.requirements_sha256:
        raise ValueError("依赖锁摘要不一致")
    abi = ports.current_abi()
    base_id = compute_base_id(info.requirements_sha256, abi)
    runtime_root = _absolute_lexical(Path(root))
    with ports.id_lock(runtime_root, "base", base_id, shared=False) as bound_root:
        return _run_bound_build(
            bound_root,
            runtime_root=runtime_root,
            lock_path=lock_path,
            contract=contract,
            abi=abi,
            base_id=base_id,
            wheelhouse=(None if wheelhouse is None else Path(wheelhouse).expanduser().absolute()),
        )


@runtime_operation_timeout(120.0)
def verify_base(root: Path, base_id: str) -> BaseMetadata:
    """在共享对象锁内完整复验一个已完成基座。"""
    runtime_root, object_id, ports = _verification_context(root, base_id)
    with ports.id_lock(runtime_root, "base", object_id, shared=True) as bound_root:
        return _run_bound_verify(
            bound_root,
            runtime_root=runtime_root,
            base_id=object_id,
            deep=False,
        )


def verify_base_locked(root: Path, base_id: str) -> BaseMetadata:
    """复验调用方已经持有 ID 锁的基座，不重复获取同一把锁。"""
    runtime_root, object_id, ports = _verification_context(root, base_id)
    return _verify_locked(runtime_root, object_id, ports)


@runtime_operation_timeout(900.0)
def verify_base_deep(root: Path, base_id: str) -> BaseMetadata:
    """显式执行一次有副作用隔离的深验证；不进入激活热路径。"""
    runtime_root, object_id, ports = _verification_context(root, base_id)
    with ports.id_lock(runtime_root, "base", object_id, shared=True) as bound_root:
        return _run_bound_verify(
            bound_root,
            runtime_root=runtime_root,
            base_id=object_id,
            deep=True,
        )


def _verification_context(root: Path, base_id: str) -> tuple[Path, str, _BasePorts]:
    try:
        object_id = require_sha256(base_id, field="base_id")
    except ValueError:
        raise RuntimeBaseIntegrityError("基座 ID 无效") from None
    runtime_root = _absolute_lexical(Path(root))
    return runtime_root, object_id, _default_ports()


def _build_locked(
    root: Path,
    bound_root: object,
    lock: Path,
    contract: RequirementsLockContract,
    abi: RuntimeAbi,
    base_id: str,
    ports: _BasePorts,
    wheelhouse: Path | None,
) -> BaseMetadata:
    info = contract.info
    bases = root / "bases"
    _ensure_directory(bases)
    base_dir = bases / base_id
    marker = base_dir / _MARKER_NAME
    if base_dir.exists() or base_dir.is_symlink():
        metadata_path = base_dir / _METADATA_NAME
        if marker.exists() or marker.is_symlink() or not metadata_path.exists():
            ports.isolate_incomplete(bound_root, "base", base_id)
            if base_dir.exists() or base_dir.is_symlink():
                raise RuntimeBaseIntegrityError("未完成基座隔离后仍占用最终路径")
        else:
            try:
                ports.verify_object_access(base_dir)
                metadata = _read_metadata(base_dir)
                existing = _verify_content(
                    root,
                    base_dir,
                    base_id,
                    metadata,
                    metadata.abi,
                    ports,
                )
            except RuntimeBaseIntegrityError:
                ports.isolate_corrupt(bound_root, "base", base_id)
                if base_dir.exists() or base_dir.is_symlink():
                    raise RuntimeBaseIntegrityError("损坏基座隔离后仍占用最终路径") from None
            else:
                _require_same_build_input(existing, info, abi)
                return existing
    base_dir.mkdir()
    marker = base_dir / _MARKER_NAME
    _write_stage(marker, "after_marker")
    ports.fsync_directory(base_dir)
    saved_lock = base_dir / _LOCK_RELATIVE
    _copy_lock_verified(lock, saved_lock, info.requirements_sha256)
    layout = ports.create_venv(base_dir)
    _write_stage(marker, "after_venv")
    _require_venv_layout(base_dir, layout)
    ports.verify_execution_trust(root, base_dir, layout.python, layout.purelib, layout.bin_dir)
    offline = None
    if wheelhouse is not None:
        offline = ports.verify_wheelhouse(contract, wheelhouse)
    source_arguments = ("--no-index", "--find-links", str(offline)) if offline is not None else ()
    _run_isolated(
        ports.run_python,
        layout.python,
        (
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "--no-input",
            "install",
            *source_arguments,
            "--require-hashes",
            "--no-compile",
            "-r",
            str(saved_lock),
        ),
    )
    if offline is not None:
        ports.verify_wheelhouse(contract, offline)
    _write_stage(marker, "after_install")
    ports.verify_execution_trust(root, base_dir, layout.python, layout.purelib, layout.bin_dir)
    ports.seal_object_access(base_dir)
    inventory_before = _verify_inventory(ports, layout.purelib, contract.pins)
    freeze = _run_deep_probes(ports, layout.python)
    inventory_after = _verify_inventory(ports, layout.purelib, contract.pins)
    if inventory_after != inventory_before:
        raise RuntimeBaseIntegrityError("基座动态探针期间 purelib 清单发生漂移")
    metadata = BaseMetadata(
        schema_version=3,
        access_profile=RUNTIME_ACCESS_PROFILE,
        base_id=base_id,
        requirements_sha256=info.requirements_sha256,
        approved_index_url=info.approved_index_url,
        artifact_manifest_sha256=info.artifact_manifest_sha256,
        freeze_sha256=hashlib.sha256(freeze).hexdigest(),
        purelib_inventory_sha256=inventory_after.inventory_sha256,
        abi=abi,
        created_at=ports.utc_now(),
        python_relative=_relative(layout.python, base_dir),
        purelib_relative=_relative(layout.purelib, base_dir),
        bin_relative=_relative(layout.bin_dir, base_dir),
        lock_relative=_LOCK_RELATIVE,
    )
    write_base_metadata_atomic(base_dir / _METADATA_NAME, metadata)
    _write_stage(marker, "after_base_json")
    ports.seal_object_access(base_dir)
    persisted = _verify_locked(root, base_id, ports, allow_incomplete=True)
    _require_same_build_input(persisted, info, abi)
    if persisted != metadata:
        raise RuntimeBaseIntegrityError("基座元数据落盘后发生漂移")
    ports.seal_durable_tree(base_dir)
    marker.unlink()
    ports.fsync_directory(base_dir)
    ports.fsync_directory(bases)
    return metadata


def _require_same_build_input(
    metadata: BaseMetadata,
    info: RequirementsLockInfo,
    abi: RuntimeAbi,
) -> None:
    if (
        metadata.requirements_sha256 != info.requirements_sha256
        or metadata.approved_index_url != info.approved_index_url
        or metadata.artifact_manifest_sha256 != info.artifact_manifest_sha256
        or metadata.abi != abi
    ):
        raise RuntimeIdCollisionError("基座 ID 与构建输入发生碰撞")


def _verify_locked(
    root: Path,
    base_id: str,
    ports: _BasePorts,
    *,
    allow_incomplete: bool = False,
) -> BaseMetadata:
    base_dir = root / "bases" / base_id
    if base_dir.is_symlink() or not base_dir.is_dir():
        raise RuntimeBaseIntegrityError("基座目录无效")
    marker = base_dir / _MARKER_NAME
    if marker.exists() or marker.is_symlink():
        if not allow_incomplete or _read_stage(marker) != "after_base_json":
            raise RuntimeBaseIntegrityError("基座尚未完成")
    ports.verify_object_access(base_dir)
    metadata = _read_metadata(base_dir)
    abi = ports.current_abi()
    return _verify_content(root, base_dir, base_id, metadata, abi, ports)


def _read_metadata(base_dir: Path) -> BaseMetadata:
    metadata_path = base_dir / _METADATA_NAME
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise RuntimeBaseIntegrityError("基座元数据不存在")
    try:
        return read_base_metadata(metadata_path)
    except ValueError:
        raise RuntimeBaseIntegrityError("基座元数据无效") from None


def _verify_content(
    root: Path,
    base_dir: Path,
    base_id: str,
    metadata: BaseMetadata,
    abi: RuntimeAbi,
    ports: _BasePorts,
) -> BaseMetadata:
    lock_path = _managed_file(base_dir, metadata.lock_relative, "依赖锁")
    requirements_sha = sha256_file(lock_path)
    try:
        inspected_contract = ports.inspect_lock(lock_path, metadata.approved_index_url)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeBaseIntegrityError("基座依赖锁无法复验") from None
    if not isinstance(inspected_contract, RequirementsLockContract):
        raise RuntimeBaseIntegrityError("基座依赖锁复验结果无效")
    inspected_lock = inspected_contract.info
    if (
        metadata.requirements_sha256 != requirements_sha
        or inspected_lock.requirements_sha256 != requirements_sha
        or inspected_lock.approved_index_url != metadata.approved_index_url
        or inspected_lock.artifact_manifest_sha256 != metadata.artifact_manifest_sha256
    ):
        raise RuntimeBaseIntegrityError("基座依赖锁元数据不一致")
    if (
        metadata.base_id != base_id
        or metadata.abi != abi
        or compute_base_id(requirements_sha, abi) != base_id
        or metadata.lock_relative != _LOCK_RELATIVE
    ):
        raise RuntimeBaseIntegrityError("基座身份不一致")
    python = _managed_file(base_dir, metadata.python_relative, "基座解释器", allow_symlink=True)
    purelib = _managed_directory(base_dir, metadata.purelib_relative, "基座 purelib")
    bin_dir = _managed_directory(base_dir, metadata.bin_relative, "基座可执行目录")
    if python.parent != bin_dir:
        raise RuntimeBaseIntegrityError("基座解释器布局不一致")
    ports.verify_execution_trust(root, base_dir, python, purelib, bin_dir)
    inventory = _verify_inventory(ports, purelib, inspected_contract.pins)
    if inventory.inventory_sha256 != metadata.purelib_inventory_sha256:
        raise RuntimeBaseIntegrityError("基座 purelib 静态清单漂移")
    return metadata


def _verify_inventory(
    ports: _BasePorts,
    purelib: Path,
    pins: tuple[DistributionPin, ...],
) -> DistributionInventoryProof:
    try:
        proof = ports.verify_inventory(purelib, pins)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeBaseIntegrityError("基座 purelib 静态清单无法验证") from None
    if not isinstance(proof, DistributionInventoryProof):
        raise RuntimeBaseIntegrityError("基座 purelib 静态清单证明类型无效")
    return proof


def _run_deep_probes(ports: _BasePorts, python: Path) -> bytes:
    _run_isolated(ports.run_python, python, ("-m", "pip", "check"))
    _run_isolated(ports.run_python, python, ("-c", _import_probe()))
    return _run_isolated(ports.run_python, python, ("-m", "pip", "freeze", "--all"))


def _read_stage(marker: Path) -> str:
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeBaseIntegrityError("基座完成标记无效")
    try:
        value = marker.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise RuntimeBaseIntegrityError("基座完成标记无效") from None
    if value != "after_base_json\n":
        raise RuntimeBaseIntegrityError("基座完成标记阶段无效")
    return value.removesuffix("\n")


def _verify_execution_trust(
    root: Path,
    base_dir: Path,
    python: Path,
    purelib: Path,
    bin_dir: Path,
) -> None:
    """在执行基座 Python 前证明路径不可被非特权用户替换。"""
    from codev_platform.runtime_execution_trust import (
        RuntimeExecutionTrustError,
        verify_execution_trust,
    )

    try:
        verify_execution_trust(
            root,
            base_dir,
            ((python, True), (purelib, False), (bin_dir, False)),
        )
    except RuntimeExecutionTrustError as error:
        raise RuntimeBaseIntegrityError(str(error)) from None


def _seal_object_access(base_dir: Path) -> None:
    from codev_platform.runtime_object_access import (
        RuntimeObjectAccessError,
        seal_runtime_object_access,
    )

    try:
        seal_runtime_object_access(base_dir)
    except RuntimeObjectAccessError:
        raise RuntimeBaseIntegrityError("基座对象访问模式无法安全定型") from None


def _verify_object_access(base_dir: Path) -> None:
    from codev_platform.runtime_object_access import (
        RuntimeObjectAccessError,
        verify_runtime_object_access,
    )

    try:
        verify_runtime_object_access(base_dir)
    except RuntimeObjectAccessError:
        raise RuntimeBaseIntegrityError("基座对象访问模式无法复验") from None


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _import_probe() -> str:
    modules = repr(RUNTIME_IMPORT_MODULES)
    return (
        "import importlib; "
        f"[importlib.import_module(name) for name in {modules}]; "
        "from importlib import metadata; "
        "assert all(dist.metadata['Name'].lower() != 'codev-platform' "
        "for dist in metadata.distributions())"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_privileged_builder() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeBaseError("依赖基座构建必须由 POSIX root 执行")


__all__ = [
    "RuntimeBaseError",
    "RuntimeBaseIntegrityError",
    "RuntimeIdCollisionError",
    "build_base",
    "verify_base",
    "verify_base_deep",
    "verify_base_locked",
]
