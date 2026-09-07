"""基座构建测试共享的受控端口替身。"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from codev_platform.core.runtime_models import RequirementsLockInfo, RuntimeAbi
from codev_platform.runtime_dependency_contract import (
    DistributionPin,
    LockedWheelArtifact,
    RequirementsLockContract,
)
from codev_platform.runtime_distribution_inventory import DistributionInventoryProof


def _abi() -> RuntimeAbi:
    return RuntimeAbi(
        implementation="cpython",
        python_version="3.11.9",
        cache_tag="cpython-311",
        soabi="cpython-311-x86_64-linux-gnu",
        platform_tag="linux-x86_64",
        machine="x86_64",
    )


def _lock_info(content: bytes) -> RequirementsLockInfo:
    return RequirementsLockInfo(
        requirements_sha256=hashlib.sha256(content).hexdigest(),
        approved_index_url="https://download.pytorch.org/whl/cu128",
        pin_count=1,
        artifact_manifest_sha256="a" * 64,
        cuda_tags=frozenset({"cu128"}),
    )


def _install_fake_ports(
    monkeypatch,
    module,
    lock_info: RequirementsLockInfo,
    *,
    events: list[object] | None = None,
    freeze: bytes = b"demo==1.0\n",
    isolate_incomplete=None,
    isolate_corrupt=None,
    create_venv_override=None,
    run_python_override=None,
    verify_inventory_override=None,
    verify_execution_trust_override=None,
    seal_object_access_override=None,
    verify_object_access_override=None,
    inspected_lock_info: RequirementsLockInfo | None = None,
    runtime_abi: RuntimeAbi | None = None,
) -> SimpleNamespace:
    """安装不触发真实 venv 或 pip 的基座构建端口。"""
    recorded = [] if events is None else events
    monkeypatch.setattr(module, "_require_privileged_builder", lambda: None)

    @contextmanager
    def object_lock(_root: Path, _kind: str, _object_id: str, *, shared: bool):
        recorded.append(("lock", _kind, _object_id, shared))
        yield object()

    def create_venv(base_dir: Path):
        recorded.append(("create_venv", base_dir))
        python = base_dir / "venv" / "bin" / "python"
        purelib = base_dir / "venv" / "lib" / "python3.11" / "site-packages"
        python.parent.mkdir(parents=True)
        python.write_bytes(b"python")
        purelib.mkdir(parents=True)
        return module._VenvLayout(python=python, purelib=purelib, bin_dir=python.parent)

    def run_python(_python: Path, arguments: tuple[str, ...]) -> bytes:
        recorded.append(("python", _python, arguments))
        if arguments == ("-B", "-I", "-m", "pip", "freeze", "--all"):
            return freeze
        return b""

    def validate_lock(lock: Path, approved: Path) -> RequirementsLockContract:
        recorded.append(("validate_lock", lock, approved))
        return RequirementsLockContract(
            info=lock_info,
            pins=(DistributionPin("demo", "1.0"),),
            artifacts=(LockedWheelArtifact("demo", "demo-1.0-py3-none-any.whl", "a" * 64),),
        )

    def inspect_lock(lock: Path, expected_index: str) -> RequirementsLockContract:
        recorded.append(("inspect_lock", lock, expected_index))
        selected = lock_info if inspected_lock_info is None else inspected_lock_info
        return RequirementsLockContract(
            info=selected,
            pins=(DistributionPin("demo", "1.0"),),
            artifacts=(LockedWheelArtifact("demo", "demo-1.0-py3-none-any.whl", "a" * 64),),
        )

    def verify_inventory(
        purelib: Path,
        pins: tuple[DistributionPin, ...],
    ) -> DistributionInventoryProof:
        recorded.append(("inventory", purelib, pins))
        return DistributionInventoryProof(
            inventory_sha256="e" * 64,
            distributions=("demo==1.0",),
            file_count=1,
            total_bytes=1,
        )

    ports = SimpleNamespace(
        validate_lock=validate_lock,
        inspect_lock=inspect_lock,
        current_abi=_abi if runtime_abi is None else (lambda: runtime_abi),
        id_lock=object_lock,
        isolate_incomplete=(
            (lambda _root, _kind, _object_id: None)
            if isolate_incomplete is None
            else isolate_incomplete
        ),
        isolate_corrupt=(
            (lambda _root, _kind, _object_id: None) if isolate_corrupt is None else isolate_corrupt
        ),
        fsync_directory=lambda _path: None,
        seal_durable_tree=lambda path: recorded.append(("seal_durable_tree", path)),
        seal_object_access=(
            (lambda path: recorded.append(("seal_object_access", path)))
            if seal_object_access_override is None
            else seal_object_access_override
        ),
        verify_object_access=(
            (lambda path: recorded.append(("verify_object_access", path)))
            if verify_object_access_override is None
            else verify_object_access_override
        ),
        verify_inventory=(
            verify_inventory if verify_inventory_override is None else verify_inventory_override
        ),
        verify_wheelhouse=lambda _contract, path: (
            recorded.append(("wheelhouse", path)),
            path,
        )[1],
        create_venv=create_venv if create_venv_override is None else create_venv_override,
        run_python=run_python if run_python_override is None else run_python_override,
        verify_execution_trust=(
            (lambda _root, _base, _python, _purelib, _bin_dir: None)
            if verify_execution_trust_override is None
            else verify_execution_trust_override
        ),
        utc_now=lambda: "2026-07-17T00:00:00Z",
    )

    def direct_bound_build(
        lock_capability: object,
        *,
        runtime_root: Path,
        lock_path: Path,
        contract: RequirementsLockContract,
        abi: RuntimeAbi,
        base_id: str,
        wheelhouse: Path | None,
    ):
        return module._build_locked(
            runtime_root,
            lock_capability,
            lock_path,
            contract,
            abi,
            base_id,
            ports,
            wheelhouse,
        )

    def direct_bound_verify(
        _lock_capability: object,
        *,
        runtime_root: Path,
        base_id: str,
        deep: bool,
    ):
        metadata = module._verify_locked(runtime_root, base_id, ports)
        if not deep:
            return metadata
        base_dir = runtime_root / "bases" / base_id
        python = module._managed_file(
            base_dir,
            metadata.python_relative,
            "基座解释器",
            allow_symlink=True,
        )
        freeze = module._run_deep_probes(ports, python)
        if hashlib.sha256(freeze).hexdigest() != metadata.freeze_sha256:
            raise module.RuntimeBaseIntegrityError("基座 freeze 摘要漂移")
        return module._verify_locked(runtime_root, base_id, ports)

    monkeypatch.setattr(module, "_run_bound_build", direct_bound_build)
    monkeypatch.setattr(module, "_run_bound_verify", direct_bound_verify)
    monkeypatch.setattr(module, "_default_ports", lambda: ports)
    return ports
