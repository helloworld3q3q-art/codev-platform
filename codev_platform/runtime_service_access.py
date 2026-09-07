"""运行时父命名空间与内容对象的服务主组只读发布门面。"""

from __future__ import annotations

import os as os
from contextlib import ExitStack
from pathlib import Path

from codev_platform import _runtime_service_content as _content
from codev_platform import _runtime_service_namespace as _namespace
from codev_platform import _runtime_service_access_validation as _validation
from codev_platform._runtime_service_access_contracts import (
    RuntimeServiceAccessError,
    RuntimeServiceAccessProof,
    RuntimeServiceContentProof,
    RuntimeServiceNamespaceProof,
    _ContentRequest,
    _NamespaceEntry,
    _ServiceAccessPorts,
    _StaticSnapshot,
)
from codev_platform.runtime_fd_tree import (
    PreflightGroup as PreflightGroup,
    PublishGroup as PublishGroup,
    RuntimeFdGroupPolicy,
    RuntimeFdTreeReport,
    VerifyGroup as VerifyGroup,
    walk_runtime_tree as walk_runtime_tree,
)


def converge_runtime_service_namespace(
    root: Path,
    *,
    service_uid: int,
    service_gid: int,
) -> RuntimeServiceNamespaceProof:
    """全量预检后只把 runtime/bases/releases 收敛为 root:服务组 0710。"""
    uid, gid = _require_service_identity(service_uid, service_gid)
    _require_linux_root()
    runtime_root = _normalize_runtime_root(root)
    try:
        with ExitStack() as stack:
            entries = _open_namespace_entries(
                stack,
                runtime_root,
                target_gid=gid,
                require_target=False,
            )
            _publish_namespace_gid(entries, root=runtime_root, target_gid=gid)
            _publish_namespace_mode(entries, root=runtime_root, target_gid=gid)
            _verify_namespace_references(entries, runtime_root)
    except RuntimeServiceAccessError:
        raise
    except (OSError, TypeError, ValueError):
        raise RuntimeServiceAccessError("运行时服务命名空间无法安全收敛") from None
    return _namespace_proof(uid, gid)


def publish_runtime_service_objects(
    root: Path,
    *,
    service_uid: int,
    service_gid: int,
    base_ids: tuple[str, ...],
    release_ids: tuple[str, ...],
) -> RuntimeServiceContentProof:
    """在完整排他锁域内把精确列出的已完成对象发布到服务主组。"""
    request = _content_request(
        root,
        service_uid=service_uid,
        service_gid=service_gid,
        base_ids=base_ids,
        release_ids=release_ids,
    )
    _verify_namespace(request.root, request.service_uid, request.service_gid)
    content = _access_content_objects(request, _default_ports(), publish=True)
    _verify_namespace(request.root, request.service_uid, request.service_gid)
    return content


def verify_runtime_service_access(
    root: Path,
    *,
    service_uid: int,
    service_gid: int,
    base_ids: tuple[str, ...],
    release_ids: tuple[str, ...],
) -> RuntimeServiceAccessProof:
    """只读复验父命名空间、静态对象身份与精确服务主组。"""
    request = _content_request(
        root,
        service_uid=service_uid,
        service_gid=service_gid,
        base_ids=base_ids,
        release_ids=release_ids,
    )
    _verify_namespace(request.root, request.service_uid, request.service_gid)
    content = _access_content_objects(request, _default_ports(), publish=False)
    namespace = _verify_namespace(request.root, request.service_uid, request.service_gid)
    return RuntimeServiceAccessProof(namespace=namespace, content=content)


# 以下薄适配保留原模块内的测试接缝，同时把实现依赖显式传给职责模块。
_namespace_proof = _namespace._namespace_proof
_open_namespace_entries = _namespace._open_namespace_entries
_namespace_entry = _namespace._namespace_entry
_namespace_reference = _namespace._namespace_reference
_validate_namespace_metadata = _namespace._validate_namespace_metadata
_require_no_extended_attributes = _namespace._require_no_extended_attributes
_open_directory = _namespace._open_directory
_stat_at = _namespace._stat_at
_require_same_namespace_entry = _namespace._require_same_namespace_entry
_require_namespace_gid_change = _namespace._require_namespace_gid_change
_require_namespace_mode_change = _namespace._require_namespace_mode_change
_namespace_identity = _namespace._namespace_identity
_namespace_gid_identity = _namespace._namespace_gid_identity
_namespace_mode_identity = _namespace._namespace_mode_identity


def _verify_namespace(
    root: Path,
    service_uid: int,
    service_gid: int,
) -> RuntimeServiceNamespaceProof:
    return _namespace._verify_namespace(root, service_uid, service_gid)


def _publish_namespace_gid(
    entries: tuple[_NamespaceEntry, ...],
    *,
    root: Path,
    target_gid: int,
) -> None:
    _namespace._publish_namespace_gid(
        entries,
        root=root,
        target_gid=target_gid,
        namespace_reference=_namespace_reference,
    )


def _publish_namespace_mode(
    entries: tuple[_NamespaceEntry, ...],
    *,
    root: Path,
    target_gid: int,
) -> None:
    _namespace._publish_namespace_mode(
        entries,
        root=root,
        target_gid=target_gid,
        namespace_reference=_namespace_reference,
    )


def _verify_namespace_references(
    entries: tuple[_NamespaceEntry, ...],
    root: Path,
) -> None:
    _namespace._verify_namespace_references(
        entries,
        root,
        namespace_reference=_namespace_reference,
    )


def _rebind_namespace_entry(
    entries: tuple[_NamespaceEntry, ...],
    entry: _NamespaceEntry,
    root: Path,
    *,
    target_gid: int,
    require_target_gid: bool,
) -> os.stat_result:
    return _namespace._rebind_namespace_entry(
        entries,
        entry,
        root,
        target_gid=target_gid,
        require_target_gid=require_target_gid,
        namespace_reference=_namespace_reference,
    )


_require_service_identity = _validation._require_service_identity
_require_object_ids = _validation._require_object_ids
_require_linux_root = _validation._require_linux_root
_normalize_runtime_root = _validation._normalize_runtime_root
_require_nonzero_sha256 = _validation._require_nonzero_sha256
_content_object_roots = _content._content_object_roots


def _content_request(
    root: Path,
    *,
    service_uid: int,
    service_gid: int,
    base_ids: tuple[str, ...],
    release_ids: tuple[str, ...],
) -> _ContentRequest:
    return _content._content_request(
        root,
        service_uid=service_uid,
        service_gid=service_gid,
        base_ids=base_ids,
        release_ids=release_ids,
        require_linux_root=_require_linux_root,
    )


def _access_content_objects(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    publish: bool,
) -> RuntimeServiceContentProof:
    return _content._access_content_objects(
        request,
        ports,
        publish=publish,
        access_content_locked=_access_content_locked,
    )


def _access_content_locked(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    release_bases: tuple[str, ...],
    publish: bool,
) -> RuntimeServiceContentProof:
    return _content._access_content_locked(
        request,
        ports,
        release_bases=release_bases,
        publish=publish,
        static_snapshot=_static_snapshot,
        metadata_sha256_snapshot=_metadata_sha256_snapshot,
        walk_tree=walk_runtime_tree,
    )


def _preflight_content_objects(
    request: _ContentRequest,
    policy: RuntimeFdGroupPolicy,
) -> tuple[RuntimeFdTreeReport, ...]:
    return _content._preflight_content_objects(
        request,
        policy,
        walk_tree=walk_runtime_tree,
    )


def _walk_bound_content_objects(
    request: _ContentRequest,
    policy: RuntimeFdGroupPolicy,
    preflight: tuple[RuntimeFdTreeReport, ...],
    *,
    publish: bool,
    phase: str,
) -> tuple[RuntimeFdTreeReport, ...]:
    return _content._walk_bound_content_objects(
        request,
        policy,
        preflight,
        publish=publish,
        phase=phase,
        walk_tree=walk_runtime_tree,
    )


def _static_snapshot(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    release_bases: tuple[str, ...],
) -> _StaticSnapshot:
    return _content._static_snapshot(
        request,
        ports,
        release_bases=release_bases,
        metadata_sha256_snapshot=_metadata_sha256_snapshot,
    )


def _metadata_sha256_snapshot(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return _content._metadata_sha256_snapshot(request, ports)


def _default_ports() -> _ServiceAccessPorts:
    from codev_platform.core.runtime_models import sha256_file
    from codev_platform.runtime_base import verify_base_locked
    from codev_platform.runtime_build import (
        read_release_base_id_locked,
        verify_release_locked,
    )
    from codev_platform.runtime_storage import id_lock

    return _ServiceAccessPorts(
        id_lock=id_lock,
        read_release_base_id_locked=read_release_base_id_locked,
        verify_base_locked=verify_base_locked,
        verify_release_locked=verify_release_locked,
        sha256_file=sha256_file,
    )


__all__ = [
    "RuntimeServiceAccessError",
    "RuntimeServiceAccessProof",
    "RuntimeServiceContentProof",
    "RuntimeServiceNamespaceProof",
    "converge_runtime_service_namespace",
    "publish_runtime_service_objects",
    "verify_runtime_service_access",
]
