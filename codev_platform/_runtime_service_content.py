"""运行时内容对象的锁域发布、只读复验与证明生成。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from codev_platform._runtime_service_access_contracts import (
    RuntimeServiceAccessError,
    RuntimeServiceContentProof,
    _ContentRequest,
    _ServiceAccessPorts,
    _StaticSnapshot,
)
from codev_platform._runtime_service_access_validation import (
    _normalize_runtime_root,
    _require_linux_root,
    _require_nonzero_sha256,
    _require_object_ids,
    _require_service_identity,
)
from codev_platform.core.runtime_models import RUNTIME_ACCESS_PROFILE
from codev_platform.runtime_fd_tree import (
    PreflightGroup,
    PublishGroup,
    RuntimeFdGroupPolicy,
    RuntimeFdTreeError,
    RuntimeFdTreeOperation,
    RuntimeFdTreeReport,
    VerifyGroup,
    walk_runtime_tree,
)
from codev_platform.runtime_object_access import (
    RUNTIME_OBJECT_MARKER_POLICY,
    RUNTIME_OBJECT_MODE_POLICY,
)


_LOGGER = logging.getLogger("codev_platform.runtime_service_access")
_WalkTree = Callable[[Path, RuntimeFdTreeOperation], RuntimeFdTreeReport]


def _content_request(
    root: Path,
    *,
    service_uid: int,
    service_gid: int,
    base_ids: tuple[str, ...],
    release_ids: tuple[str, ...],
    require_linux_root: Callable[[], None] = _require_linux_root,
) -> _ContentRequest:
    uid, gid = _require_service_identity(service_uid, service_gid)
    bases = _require_object_ids(base_ids, "base")
    releases = _require_object_ids(release_ids, "release")
    require_linux_root()
    return _ContentRequest(
        root=_normalize_runtime_root(root),
        service_uid=uid,
        service_gid=gid,
        base_ids=bases,
        release_ids=releases,
    )


def _access_content_objects(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    publish: bool,
    access_content_locked: Callable[..., RuntimeServiceContentProof] | None = None,
) -> RuntimeServiceContentProof:
    locked_access = access_content_locked or _access_content_locked
    try:
        with ExitStack() as locks:
            for release_id in request.release_ids:
                locks.enter_context(
                    ports.id_lock(
                        request.root,
                        "release",
                        release_id,
                        shared=not publish,
                    )
                )
            release_bases = tuple(
                _require_nonzero_sha256(
                    ports.read_release_base_id_locked(request.root, release_id),
                    "base_id",
                )
                for release_id in request.release_ids
            )
            if tuple(sorted(set(release_bases))) != request.base_ids:
                raise RuntimeServiceAccessError("运行时服务 base 集合与 release 引用不一致")
            for base_id in request.base_ids:
                locks.enter_context(
                    ports.id_lock(
                        request.root,
                        "base",
                        base_id,
                        shared=not publish,
                    )
                )
            return locked_access(
                request,
                ports,
                release_bases=release_bases,
                publish=publish,
            )
    except RuntimeServiceAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
        raise RuntimeServiceAccessError("运行时服务内容对象锁定或验证失败") from None


def _access_content_locked(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    release_bases: tuple[str, ...],
    publish: bool,
    static_snapshot: Callable[..., _StaticSnapshot] | None = None,
    metadata_sha256_snapshot: Callable[
        [_ContentRequest, _ServiceAccessPorts],
        tuple[tuple[str, ...], tuple[str, ...]],
    ]
    | None = None,
    walk_tree: _WalkTree | None = None,
) -> RuntimeServiceContentProof:
    snapshot = static_snapshot or _static_snapshot
    metadata_snapshot = metadata_sha256_snapshot or _metadata_sha256_snapshot
    walker = walk_tree or walk_runtime_tree
    before = snapshot(request, ports, release_bases=release_bases)
    policy = RuntimeFdGroupPolicy(target_gid=request.service_gid)
    preflight = _preflight_content_objects(request, policy, walk_tree=walker)
    if publish:
        _walk_bound_content_objects(
            request,
            policy,
            preflight,
            publish=True,
            phase="发布",
            walk_tree=walker,
        )
    reports = _walk_bound_content_objects(
        request,
        policy,
        preflight,
        publish=False,
        phase="复验",
        walk_tree=walker,
    )
    base_metadata, release_metadata = metadata_snapshot(request, ports)
    if (
        base_metadata != before.base_metadata_sha256
        or release_metadata != before.release_metadata_sha256
    ):
        raise RuntimeServiceAccessError("运行时服务内容对象静态身份发生漂移")
    return RuntimeServiceContentProof(
        access_profile=RUNTIME_ACCESS_PROFILE,
        service_uid=request.service_uid,
        service_gid=request.service_gid,
        base_ids=request.base_ids,
        release_ids=request.release_ids,
        base_metadata_sha256=before.base_metadata_sha256,
        base_inventory_sha256=before.base_inventory_sha256,
        release_metadata_sha256=before.release_metadata_sha256,
        entries=sum(report.entries for report in reports),
        total_bytes=sum(report.total_bytes for report in reports),
    )


def _content_object_roots(request: _ContentRequest) -> tuple[Path, ...]:
    return tuple(
        [request.root / "bases" / base_id for base_id in request.base_ids]
        + [request.root / "releases" / release_id for release_id in request.release_ids]
    )


def _preflight_content_objects(
    request: _ContentRequest,
    policy: RuntimeFdGroupPolicy,
    *,
    walk_tree: _WalkTree = walk_runtime_tree,
) -> tuple[RuntimeFdTreeReport, ...]:
    operation = PreflightGroup(
        mode_policy=RUNTIME_OBJECT_MODE_POLICY,
        marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        group_policy=policy,
    )
    reports: list[RuntimeFdTreeReport] = []
    try:
        roots = _content_object_roots(request)
        for index, root in enumerate(roots, start=1):
            _LOGGER.info("运行时服务内容对象预检：%d/%d", index, len(roots))
            reports.append(walk_tree(root, operation))
    except RuntimeFdTreeError:
        raise RuntimeServiceAccessError("运行时服务内容对象预检失败") from None
    return tuple(reports)


def _walk_bound_content_objects(
    request: _ContentRequest,
    policy: RuntimeFdGroupPolicy,
    preflight: tuple[RuntimeFdTreeReport, ...],
    *,
    publish: bool,
    phase: str,
    walk_tree: _WalkTree = walk_runtime_tree,
) -> tuple[RuntimeFdTreeReport, ...]:
    roots = _content_object_roots(request)
    if len(roots) != len(preflight):
        raise RuntimeServiceAccessError("运行时服务内容对象身份快照不完整")
    reports: list[RuntimeFdTreeReport] = []
    try:
        for index, (root, expected) in enumerate(
            zip(roots, preflight, strict=True),
            start=1,
        ):
            _LOGGER.info(
                "运行时服务内容对象%s：%d/%d",
                phase,
                index,
                len(roots),
            )
            if publish:
                operation = PublishGroup(
                    mode_policy=RUNTIME_OBJECT_MODE_POLICY,
                    marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
                    group_policy=policy,
                    expected_snapshot=expected.identity_snapshot,
                )
            else:
                operation = VerifyGroup(
                    mode_policy=RUNTIME_OBJECT_MODE_POLICY,
                    marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
                    group_policy=policy,
                    expected_snapshot=expected.identity_snapshot,
                )
            reports.append(walk_tree(root, operation))
    except RuntimeFdTreeError:
        raise RuntimeServiceAccessError(f"运行时服务内容对象{phase}失败") from None
    return tuple(reports)


def _static_snapshot(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
    *,
    release_bases: tuple[str, ...],
    metadata_sha256_snapshot: Callable[
        [_ContentRequest, _ServiceAccessPorts],
        tuple[tuple[str, ...], tuple[str, ...]],
    ]
    | None = None,
) -> _StaticSnapshot:
    metadata_snapshot = metadata_sha256_snapshot or _metadata_sha256_snapshot
    try:
        bases = tuple(
            ports.verify_base_locked(request.root, base_id) for base_id in request.base_ids
        )
        by_id = dict(zip(request.base_ids, bases, strict=True))
        for base_id, metadata in zip(request.base_ids, bases, strict=True):
            if (
                metadata.schema_version != 3
                or metadata.access_profile != RUNTIME_ACCESS_PROFILE
                or metadata.base_id != base_id
            ):
                raise RuntimeServiceAccessError("运行时服务 base 访问模型不受支持")
        releases = tuple(
            ports.verify_release_locked(
                request.root,
                release_id,
                verified_base=by_id[base_id],
            )
            for release_id, base_id in zip(
                request.release_ids,
                release_bases,
                strict=True,
            )
        )
        for release_id, base_id, metadata in zip(
            request.release_ids,
            release_bases,
            releases,
            strict=True,
        ):
            if (
                metadata.schema_version != 1
                or metadata.release_id != release_id
                or metadata.base_id != base_id
            ):
                raise RuntimeServiceAccessError("运行时服务 release 访问模型不受支持")
        base_metadata, release_metadata = metadata_snapshot(request, ports)
        inventories = tuple(
            _require_nonzero_sha256(metadata.purelib_inventory_sha256, "inventory_sha256")
            for metadata in bases
        )
        return _StaticSnapshot(
            base_models=bases,
            release_models=releases,
            base_metadata_sha256=base_metadata,
            base_inventory_sha256=inventories,
            release_metadata_sha256=release_metadata,
        )
    except RuntimeServiceAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError, KeyError):
        raise RuntimeServiceAccessError("运行时服务内容对象静态验证失败") from None


def _metadata_sha256_snapshot(
    request: _ContentRequest,
    ports: _ServiceAccessPorts,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        base_metadata = tuple(
            _require_nonzero_sha256(
                ports.sha256_file(request.root / "bases" / base_id / "base.json"),
                "base_metadata_sha256",
            )
            for base_id in request.base_ids
        )
        release_metadata = tuple(
            _require_nonzero_sha256(
                ports.sha256_file(request.root / "releases" / release_id / "release.json"),
                "release_metadata_sha256",
            )
            for release_id in request.release_ids
        )
        return base_metadata, release_metadata
    except RuntimeServiceAccessError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
        raise RuntimeServiceAccessError("运行时服务对象元数据摘要复验失败") from None
