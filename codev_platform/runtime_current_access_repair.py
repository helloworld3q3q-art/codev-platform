"""只修复 current 已完成 runtime 对象的受控访问投影。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    require_sha256,
)
from codev_platform.runtime_fd_tree import (
    ConvergeCompletedAccessRepair,
    PreflightCompletedAccessRepair,
    RuntimeFdTreeAccessRepairSnapshot,
    RuntimeFdTreeReport,
    walk_runtime_tree,
)
from codev_platform.runtime_dependency_contract import DistributionPin
from codev_platform.runtime_object_access import (
    RUNTIME_OBJECT_MARKER_POLICY,
    RUNTIME_OBJECT_MODE_POLICY,
)
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_target_user_probe import RuntimeTargetUserProof


class RuntimeCurrentAccessRepairError(RuntimeError):
    """current 已完成对象访问投影无法安全修复。"""


class _VerifyBaseTolerant(Protocol):
    """把受控缓存清理选择显式注入基座复验。"""

    def __call__(
        self,
        root: Path,
        base_id: str,
        *,
        prune_generated_bytecode: bool,
    ) -> BaseMetadata: ...


class _VerifyReleaseTolerant(Protocol):
    """把受控缓存清理选择显式注入 release 静态载荷复验。"""

    def __call__(
        self,
        root: Path,
        release_id: str,
        *,
        verified_base: BaseMetadata,
        prune_generated_bytecode: bool,
    ) -> ReleaseMetadata: ...


@dataclass(frozen=True, slots=True)
class RuntimeCurrentAccessRepairProof:
    """不含路径、账号名或配置内容的修复证明。"""

    access_profile: str
    service_uid: int
    service_gid: int
    base_id: str
    release_id: str
    base_entries: int
    base_total_bytes: int
    release_entries: int
    release_total_bytes: int
    dry_run: bool
    target_user_evidence_sha256: str | None


@dataclass(frozen=True, slots=True)
class _RepairPorts:
    require_runtime_root: Callable[[Path], Path]
    activation_lock: Callable[..., AbstractContextManager[object]]
    id_lock: Callable[..., AbstractContextManager[object]]
    read_current: Callable[[Path], str]
    read_release_metadata: Callable[[Path, str], ReleaseMetadata]
    verify_base_tolerant: _VerifyBaseTolerant
    verify_release_tolerant: _VerifyReleaseTolerant
    preflight_completed_access: Callable[[Path], RuntimeFdTreeReport]
    converge_completed_access: Callable[
        [Path, RuntimeFdTreeAccessRepairSnapshot], RuntimeFdTreeReport
    ]
    verify_base_strict: Callable[[Path, str], BaseMetadata]
    verify_release_strict: Callable[..., ReleaseMetadata]
    converge_namespace: Callable[..., object]
    publish_objects: Callable[..., object]
    verify_service_access: Callable[..., object]
    probe_target_user: Callable[..., RuntimeTargetUserProof]


@dataclass(frozen=True, slots=True)
class _PreparedPair:
    root: Path
    base: BaseMetadata
    release: ReleaseMetadata
    base_report: RuntimeFdTreeReport
    release_report: RuntimeFdTreeReport


def repair_current_runtime_service_access(
    root: Path,
    *,
    account: ServiceAccount,
    dry_run: bool,
) -> RuntimeCurrentAccessRepairProof:
    """修复 current 精确引用对象，随后复用既有服务访问发布链。"""
    sys.dont_write_bytecode = True
    if type(account) is not ServiceAccount or type(dry_run) is not bool:
        raise RuntimeCurrentAccessRepairError("当前运行时访问修复参数无效")
    try:
        ports = _default_ports()
        runtime_root = ports.require_runtime_root(Path(root))
        with ports.activation_lock(runtime_root, shared=True):
            release_id = ports.read_current(runtime_root)
            prepared = _prepare_current_pair(
                runtime_root,
                release_id=release_id,
                ports=ports,
                dry_run=dry_run,
            )
            _require_current_unchanged(ports, runtime_root, release_id)
            if dry_run:
                return _proof(prepared, account=account, dry_run=True, evidence=None)
            _publish_service_access(prepared, account=account, ports=ports)
            _require_current_unchanged(ports, runtime_root, release_id)
            evidence = _probe_target_user(prepared, account=account, ports=ports)
            _require_current_unchanged(ports, runtime_root, release_id)
            return _proof(prepared, account=account, dry_run=False, evidence=evidence)
    except RuntimeCurrentAccessRepairError:
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeCurrentAccessRepairError("当前运行时访问修复失败") from None


def _prepare_current_pair(
    root: Path,
    *,
    release_id: str,
    ports: _RepairPorts,
    dry_run: bool,
) -> _PreparedPair:
    normalized_release = _require_object_id(release_id, field="current release")
    with ports.id_lock(root, "release", normalized_release, shared=False):
        release_metadata = ports.read_release_metadata(root, normalized_release)
        base_id = _require_release_reference(release_metadata, normalized_release)
        with ports.id_lock(root, "base", base_id, shared=False):
            base = ports.verify_base_tolerant(
                root,
                base_id,
                prune_generated_bytecode=False,
            )
            release = ports.verify_release_tolerant(
                root,
                normalized_release,
                verified_base=base,
                prune_generated_bytecode=False,
            )
            _require_pair_models(base, release, release_id=normalized_release, base_id=base_id)
            if not dry_run:
                base = ports.verify_base_tolerant(
                    root,
                    base_id,
                    prune_generated_bytecode=True,
                )
                release = ports.verify_release_tolerant(
                    root,
                    normalized_release,
                    verified_base=base,
                    prune_generated_bytecode=True,
                )
                _require_pair_models(
                    base,
                    release,
                    release_id=normalized_release,
                    base_id=base_id,
                )
            prepared = _PreparedPair(
                root=root,
                base=base,
                release=release,
                base_report=_require_repair_report(
                    ports.preflight_completed_access(root / "bases" / base_id)
                ),
                release_report=_require_repair_report(
                    ports.preflight_completed_access(root / "releases" / normalized_release)
                ),
            )
            if not dry_run:
                _converge_and_verify(prepared, ports=ports)
            return prepared


def _converge_and_verify(prepared: _PreparedPair, *, ports: _RepairPorts) -> None:
    base_path = prepared.root / "bases" / prepared.base.base_id
    release_path = prepared.root / "releases" / prepared.release.release_id
    _require_converged_report(
        ports.converge_completed_access(
            base_path,
            prepared.base_report.access_repair_snapshot,
        ),
        expected=prepared.base_report.access_repair_snapshot,
    )
    _require_converged_report(
        ports.converge_completed_access(
            release_path,
            prepared.release_report.access_repair_snapshot,
        ),
        expected=prepared.release_report.access_repair_snapshot,
    )
    base = ports.verify_base_strict(prepared.root, prepared.base.base_id)
    release = ports.verify_release_strict(
        prepared.root,
        prepared.release.release_id,
        verified_base=base,
    )
    _require_pair_models(
        base,
        release,
        release_id=prepared.release.release_id,
        base_id=prepared.base.base_id,
    )


def _publish_service_access(
    prepared: _PreparedPair,
    *,
    account: ServiceAccount,
    ports: _RepairPorts,
) -> None:
    namespace_arguments = {
        "service_uid": account.uid,
        "service_gid": account.gid,
    }
    content_arguments = {
        **namespace_arguments,
        "base_ids": (prepared.base.base_id,),
        "release_ids": (prepared.release.release_id,),
    }
    ports.converge_namespace(prepared.root, **namespace_arguments)
    ports.publish_objects(prepared.root, **content_arguments)
    ports.verify_service_access(prepared.root, **content_arguments)


def _probe_target_user(
    prepared: _PreparedPair,
    *,
    account: ServiceAccount,
    ports: _RepairPorts,
) -> str:
    proof = ports.probe_target_user(
        prepared.root,
        account=account,
        base_id=prepared.base.base_id,
        release_id=prepared.release.release_id,
    )
    if (
        type(proof) is not RuntimeTargetUserProof
        or proof.access_profile != RUNTIME_ACCESS_PROFILE
        or proof.service_uid != account.uid
        or proof.service_gid != account.gid
        or proof.base_id != prepared.base.base_id
        or proof.release_id != prepared.release.release_id
    ):
        raise RuntimeCurrentAccessRepairError("当前运行时服务用户探针证明无效")
    return proof.evidence_sha256


def _proof(
    prepared: _PreparedPair,
    *,
    account: ServiceAccount,
    dry_run: bool,
    evidence: str | None,
) -> RuntimeCurrentAccessRepairProof:
    return RuntimeCurrentAccessRepairProof(
        access_profile=RUNTIME_ACCESS_PROFILE,
        service_uid=account.uid,
        service_gid=account.gid,
        base_id=prepared.base.base_id,
        release_id=prepared.release.release_id,
        base_entries=prepared.base_report.entries,
        base_total_bytes=prepared.base_report.total_bytes,
        release_entries=prepared.release_report.entries,
        release_total_bytes=prepared.release_report.total_bytes,
        dry_run=dry_run,
        target_user_evidence_sha256=evidence,
    )


def _require_current_unchanged(
    ports: _RepairPorts,
    root: Path,
    expected_release_id: str,
) -> None:
    if ports.read_current(root) != expected_release_id:
        raise RuntimeCurrentAccessRepairError("current release 在访问修复期间发生漂移")


def _require_release_reference(metadata: object, release_id: str) -> str:
    if type(metadata) is not ReleaseMetadata or metadata.release_id != release_id:
        raise RuntimeCurrentAccessRepairError("current release 元数据身份无效")
    if metadata.schema_version != 1:
        raise RuntimeCurrentAccessRepairError("current release 访问模型不受支持")
    return _require_object_id(metadata.base_id, field="current base")


def _require_pair_models(
    base: object,
    release: object,
    *,
    release_id: str,
    base_id: str,
) -> None:
    if (
        type(base) is not BaseMetadata
        or base.schema_version != 3
        or base.access_profile != RUNTIME_ACCESS_PROFILE
        or base.base_id != base_id
    ):
        raise RuntimeCurrentAccessRepairError("current base 访问模型不受支持")
    if (
        type(release) is not ReleaseMetadata
        or release.schema_version != 1
        or release.release_id != release_id
        or release.base_id != base_id
    ):
        raise RuntimeCurrentAccessRepairError("current release 访问模型不受支持")


def _require_object_id(value: object, *, field: str) -> str:
    try:
        return require_sha256(value, field=field)
    except ValueError:
        raise RuntimeCurrentAccessRepairError("current runtime 对象身份无效") from None


def _require_repair_report(report: object) -> RuntimeFdTreeReport:
    if type(report) is not RuntimeFdTreeReport or report.access_repair_snapshot is None:
        raise RuntimeCurrentAccessRepairError("current runtime 访问预检证明无效")
    return report


def _require_converged_report(
    report: object,
    *,
    expected: RuntimeFdTreeAccessRepairSnapshot | None,
) -> None:
    if (
        expected is None
        or type(report) is not RuntimeFdTreeReport
        or report.access_repair_snapshot != expected
    ):
        raise RuntimeCurrentAccessRepairError("current runtime 访问收敛证明无效")


def _default_ports() -> _RepairPorts:
    from codev_platform.runtime_base import verify_base_locked
    from codev_platform.runtime_build import (
        _read_release_metadata_locked,
        verify_release_locked,
    )
    from codev_platform.runtime_release import _read_link, _require_runtime_root
    from codev_platform.runtime_service_access import (
        converge_runtime_service_namespace,
        publish_runtime_service_objects,
        verify_runtime_service_access,
    )
    from codev_platform.runtime_storage import activation_lock, id_lock
    from codev_platform.runtime_target_user_probe import probe_runtime_target_user

    def read_current(root: Path) -> str:
        current = _read_link(root, "current", required=True)
        if current is None:
            raise RuntimeCurrentAccessRepairError("current release 不存在")
        return current

    return _RepairPorts(
        require_runtime_root=_require_runtime_root,
        activation_lock=activation_lock,
        id_lock=id_lock,
        read_current=read_current,
        read_release_metadata=_read_release_metadata_locked,
        verify_base_tolerant=_verify_base_tolerant,
        verify_release_tolerant=_verify_release_tolerant,
        preflight_completed_access=_preflight_completed_access,
        converge_completed_access=_converge_completed_access,
        verify_base_strict=verify_base_locked,
        verify_release_strict=verify_release_locked,
        converge_namespace=converge_runtime_service_namespace,
        publish_objects=publish_runtime_service_objects,
        verify_service_access=verify_runtime_service_access,
        probe_target_user=probe_runtime_target_user,
    )


def _verify_base_tolerant(
    root: Path,
    base_id: str,
    *,
    prune_generated_bytecode: bool,
) -> BaseMetadata:
    from codev_platform import runtime_base
    from codev_platform.runtime_generated_bytecode import (
        apply_verified_generated_bytecode,
        plan_verified_generated_bytecode,
    )

    if type(prune_generated_bytecode) is not bool:
        raise RuntimeCurrentAccessRepairError("生成字节码清理开关无效")

    def verify_inventory(
        purelib: Path,
        pins: tuple[DistributionPin, ...],
    ):
        plan = plan_verified_generated_bytecode(purelib, pins)
        if prune_generated_bytecode:
            return apply_verified_generated_bytecode(plan).inventory_proof
        return plan.inventory_proof

    runtime_root, normalized_base_id, ports = runtime_base._verification_context(root, base_id)
    repair_ports = replace(
        ports,
        verify_object_access=_preflight_completed_access,
        verify_inventory=verify_inventory,
    )
    return runtime_base._verify_locked(runtime_root, normalized_base_id, repair_ports)


def _verify_release_tolerant(
    root: Path,
    release_id: str,
    *,
    verified_base: BaseMetadata,
    prune_generated_bytecode: bool,
) -> ReleaseMetadata:
    from codev_platform import runtime_build
    from codev_platform import runtime_release_environment
    from codev_platform.runtime_release_generated_bytecode import (
        apply_verified_release_generated_bytecode,
        plan_verified_release_generated_bytecode,
    )

    if type(prune_generated_bytecode) is not bool:
        raise RuntimeCurrentAccessRepairError("生成字节码清理开关无效")

    def verify_installed_application(
        proof: object,
        purelib: Path,
        controlled_base_pth: Path,
    ) -> None:
        plan = plan_verified_release_generated_bytecode(
            purelib,
            proof,
            controlled_base_pth,
        )
        if prune_generated_bytecode:
            apply_verified_release_generated_bytecode(plan)

    def verify_environment(
        runtime_root: Path,
        release_dir: Path,
        python: Path,
        purelib: Path,
        base_purelib: Path,
        wheel: Path,
        expected_freeze_sha256: str,
    ) -> None:
        runtime_release_environment.verify_environment(
            runtime_root,
            release_dir,
            python,
            purelib,
            base_purelib,
            wheel,
            expected_freeze_sha256,
            installed_application_verifier=verify_installed_application,
        )

    runtime_root = runtime_build._require_runtime_root(root)
    return runtime_build._verify_release_directory(
        runtime_root,
        release_id,
        base_locked=True,
        verified_base=verified_base,
        object_access_verifier=_preflight_completed_access,
        environment_verifier=verify_environment,
    )


def _preflight_completed_access(root: Path) -> RuntimeFdTreeReport:
    report = walk_runtime_tree(
        root,
        PreflightCompletedAccessRepair(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        ),
    )
    return _require_repair_report(report)


def _converge_completed_access(
    root: Path,
    snapshot: RuntimeFdTreeAccessRepairSnapshot,
) -> RuntimeFdTreeReport:
    return walk_runtime_tree(
        root,
        ConvergeCompletedAccessRepair(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
            expected_snapshot=snapshot,
        ),
    )


__all__ = [
    "RuntimeCurrentAccessRepairError",
    "RuntimeCurrentAccessRepairProof",
    "repair_current_runtime_service_access",
]
