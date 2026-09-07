"""已完成暂存 release 的服务访问投影发布。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    require_sha256,
)
from codev_platform.runtime_service_process import ServiceAccount
from codev_platform.runtime_target_user_probe import RuntimeTargetUserProof


class RuntimeStagedReleaseAccessError(RuntimeError):
    """暂存 release 的服务访问投影无法安全证明或发布。"""


@dataclass(frozen=True, slots=True)
class RuntimeStagedReleaseAccessProof:
    """不含路径、账号名或配置正文的发布回执。"""

    access_profile: str
    service_uid: int
    service_gid: int
    base_id: str
    release_id: str
    dry_run: bool
    target_user_evidence_sha256: str | None


@dataclass(frozen=True, slots=True)
class _PreparedRelease:
    root: Path
    base: BaseMetadata
    release: ReleaseMetadata


@dataclass(frozen=True, slots=True)
class _Ports:
    require_runtime_root: Callable[[Path], Path]
    activation_lock: Callable[..., AbstractContextManager[object]]
    id_lock: Callable[..., AbstractContextManager[object]]
    read_current: Callable[[Path], str]
    read_release_metadata: Callable[[Path, str], ReleaseMetadata]
    verify_base_locked: Callable[[Path, str], BaseMetadata]
    verify_release_locked: Callable[..., ReleaseMetadata]
    converge_namespace: Callable[..., object]
    publish_objects: Callable[..., object]
    verify_service_access: Callable[..., object]
    probe_target_user: Callable[..., RuntimeTargetUserProof]


def publish_staged_release_service_access(
    root: Path,
    *,
    account: ServiceAccount,
    release_id: str,
    dry_run: bool,
) -> RuntimeStagedReleaseAccessProof:
    """只发布一个未激活、已完成 release 的服务组只读访问投影。"""
    sys.dont_write_bytecode = True
    if type(account) is not ServiceAccount or type(dry_run) is not bool:
        raise RuntimeStagedReleaseAccessError("暂存 release 服务访问参数无效")
    try:
        ports = _default_ports()
        runtime_root = ports.require_runtime_root(Path(root))
        target_release_id = _require_release_id(release_id)
        with ports.activation_lock(runtime_root, shared=True):
            _require_not_current(runtime_root, target_release_id, ports)
            prepared = _prepare_release(runtime_root, target_release_id, ports)
            if dry_run:
                return _proof(prepared, account=account, dry_run=True, evidence=None)
            evidence = _publish_service_access(prepared, account=account, ports=ports)
            return _proof(prepared, account=account, dry_run=False, evidence=evidence)
    except RuntimeStagedReleaseAccessError:
        raise
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise RuntimeStagedReleaseAccessError("暂存 release 服务访问发布失败") from None


def _require_not_current(root: Path, target_release_id: str, ports: _Ports) -> None:
    current = _require_release_id(ports.read_current(root))
    if current == target_release_id:
        raise RuntimeStagedReleaseAccessError("暂存 release 服务访问不接受 current")


def _prepare_release(
    root: Path,
    release_id: str,
    ports: _Ports,
) -> _PreparedRelease:
    with ports.id_lock(root, "release", release_id, shared=False):
        metadata = ports.read_release_metadata(root, release_id)
        base_id = _require_release_metadata(metadata, release_id)
        with ports.id_lock(root, "base", base_id, shared=False):
            base = ports.verify_base_locked(root, base_id)
            release = ports.verify_release_locked(
                root,
                release_id,
                verified_base=base,
            )
            _require_verified_pair(base, release, release_id=release_id, base_id=base_id)
            return _PreparedRelease(root=root, base=base, release=release)


def _publish_service_access(
    prepared: _PreparedRelease,
    *,
    account: ServiceAccount,
    ports: _Ports,
) -> str:
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
        raise RuntimeStagedReleaseAccessError("暂存 release 服务账号探针证明无效")
    return proof.evidence_sha256


def _proof(
    prepared: _PreparedRelease,
    *,
    account: ServiceAccount,
    dry_run: bool,
    evidence: str | None,
) -> RuntimeStagedReleaseAccessProof:
    return RuntimeStagedReleaseAccessProof(
        access_profile=RUNTIME_ACCESS_PROFILE,
        service_uid=account.uid,
        service_gid=account.gid,
        base_id=prepared.base.base_id,
        release_id=prepared.release.release_id,
        dry_run=dry_run,
        target_user_evidence_sha256=evidence,
    )


def _require_release_id(value: object) -> str:
    try:
        return require_sha256(value, field="release_id")
    except ValueError:
        raise RuntimeStagedReleaseAccessError("暂存 release 身份无效") from None


def _require_release_metadata(value: object, release_id: str) -> str:
    if (
        type(value) is not ReleaseMetadata
        or value.schema_version != 1
        or value.release_id != release_id
    ):
        raise RuntimeStagedReleaseAccessError("暂存 release 元数据身份无效")
    try:
        return require_sha256(value.base_id, field="base_id")
    except ValueError:
        raise RuntimeStagedReleaseAccessError("暂存 release base 身份无效") from None


def _require_verified_pair(
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
        or type(release) is not ReleaseMetadata
        or release.schema_version != 1
        or release.release_id != release_id
        or release.base_id != base_id
    ):
        raise RuntimeStagedReleaseAccessError("暂存 release 静态对象无法证明")


def _default_ports() -> _Ports:
    from codev_platform.runtime_base import verify_base_locked
    from codev_platform.runtime_build import _read_release_metadata_locked, verify_release_locked
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
            raise RuntimeStagedReleaseAccessError("current release 不存在")
        return current

    return _Ports(
        require_runtime_root=_require_runtime_root,
        activation_lock=activation_lock,
        id_lock=id_lock,
        read_current=read_current,
        read_release_metadata=_read_release_metadata_locked,
        verify_base_locked=verify_base_locked,
        verify_release_locked=verify_release_locked,
        converge_namespace=converge_runtime_service_namespace,
        publish_objects=publish_runtime_service_objects,
        verify_service_access=verify_runtime_service_access,
        probe_target_user=probe_runtime_target_user,
    )


__all__ = [
    "RuntimeStagedReleaseAccessError",
    "RuntimeStagedReleaseAccessProof",
    "publish_staged_release_service_access",
]
