"""编排目标服务用户对不可变 release 的真实执行证明。"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
from typing import Protocol

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
)
from codev_platform.runtime_managed_process import (
    ManagedProcessResult,
    run_managed_process,
)
from codev_platform.runtime_service_process import (
    ServiceAccount,
    build_service_process_argv,
)
from codev_platform.runtime_target_user_layout import (
    TargetProbeLayout,
    TargetProbeRequest,
    TargetProbeSnapshot,
    derive_probe_layout,
    probe_environment,
    require_nonzero_sha256,
    require_probe_metadata,
    require_probe_unchanged,
    snapshot_probe_layout,
    validate_probe_request,
)
from codev_platform.runtime_target_user_protocol import (
    build_probe_command,
    require_success_output,
    run_probe_process,
)


_ERROR_MESSAGE = "目标服务用户运行时探针失败"


class RuntimeTargetUserProbeError(RuntimeError):
    """目标服务用户动态执行证明失败，错误正文固定且不含上下文。"""


@dataclass(frozen=True, slots=True)
class RuntimeTargetUserProof:
    """不包含路径、账号名或子进程内容的目标用户证明。"""

    access_profile: str
    service_uid: int
    service_gid: int
    base_id: str
    release_id: str
    runtime_revision: str
    evidence_sha256: str


class _IdLockPort(Protocol):
    def __call__(
        self,
        root: Path,
        kind: str,
        object_id: str,
        *,
        shared: bool,
    ) -> AbstractContextManager[None]: ...


class _VerifyReleasePort(Protocol):
    def __call__(
        self,
        root: Path,
        release_id: str,
        *,
        verified_base: BaseMetadata,
    ) -> ReleaseMetadata: ...


class _BuildServiceProcessPort(Protocol):
    def __call__(
        self,
        account: ServiceAccount,
        command: tuple[str, ...],
        environment: dict[str, str],
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class _ProbePorts:
    """探针编排唯一依赖的窄端口集合。"""

    id_lock: _IdLockPort
    read_release_base_id_locked: Callable[[Path, str], str]
    verify_base_locked: Callable[[Path, str], BaseMetadata]
    verify_release_locked: _VerifyReleasePort
    sha256_file: Callable[[Path], str]
    build_service_process_argv: _BuildServiceProcessPort
    run_probe: Callable[[tuple[str, ...]], ManagedProcessResult]


def probe_runtime_target_user(
    root: Path,
    *,
    account: ServiceAccount,
    base_id: str,
    release_id: str,
) -> RuntimeTargetUserProof:
    """按固定锁序静态深验，并以真实目标身份执行动态探针。"""
    failed = False
    result: RuntimeTargetUserProof | None = None
    try:
        _require_linux_root()
        request = validate_probe_request(root, account, base_id, release_id)
        result = _probe_locked(request, _default_ports())
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)
    return result


def _probe_locked(
    request: TargetProbeRequest,
    ports: _ProbePorts,
) -> RuntimeTargetUserProof:
    with ports.id_lock(request.root, "release", request.release_id, shared=True):
        referenced_base = require_nonzero_sha256(
            ports.read_release_base_id_locked(request.root, request.release_id)
        )
        if referenced_base != request.base_id:
            raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)
        with ports.id_lock(request.root, "base", request.base_id, shared=True):
            base = ports.verify_base_locked(request.root, request.base_id)
            release = ports.verify_release_locked(
                request.root,
                request.release_id,
                verified_base=base,
            )
            require_probe_metadata(request, base, release)
            layout = derive_probe_layout(request, base, release)
            before = snapshot_probe_layout(layout, ports.sha256_file)
            command = build_probe_command(request, base, release, layout)
            argv = ports.build_service_process_argv(
                request.account,
                command,
                probe_environment(layout),
            )
            completed = ports.run_probe(argv)
            _require_success_output(completed)
            final_base, final_release = _verify_after_probe(
                request,
                layout,
                before,
                ports,
            )
            return _proof(request, final_base, final_release, before)


def _verify_after_probe(
    request: TargetProbeRequest,
    before_layout: TargetProbeLayout,
    before_snapshot: TargetProbeSnapshot,
    ports: _ProbePorts,
) -> tuple[BaseMetadata, ReleaseMetadata]:
    referenced_base = require_nonzero_sha256(
        ports.read_release_base_id_locked(request.root, request.release_id)
    )
    if referenced_base != request.base_id:
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)
    base = ports.verify_base_locked(request.root, request.base_id)
    release = ports.verify_release_locked(
        request.root,
        request.release_id,
        verified_base=base,
    )
    require_probe_metadata(request, base, release)
    layout = derive_probe_layout(request, base, release)
    if layout != before_layout:
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)
    require_probe_unchanged(before_snapshot, layout, ports.sha256_file)
    return base, release


def _proof(
    request: TargetProbeRequest,
    base: BaseMetadata,
    release: ReleaseMetadata,
    snapshot: TargetProbeSnapshot,
) -> RuntimeTargetUserProof:
    payload = (
        "target-user-probe-v1\n"
        f"{RUNTIME_ACCESS_PROFILE}\n{request.account.uid}\n{request.account.gid}\n"
        f"{request.base_id}\n{request.release_id}\n{release.runtime_revision}\n"
        f"{release.wheel_sha256}\n{base.requirements_sha256}\n"
        f"{snapshot.base_metadata_sha256}\n{snapshot.base_pth_sha256}\n"
        f"{snapshot.release_metadata_sha256}\n"
    ).encode("ascii")
    return RuntimeTargetUserProof(
        access_profile=RUNTIME_ACCESS_PROFILE,
        service_uid=request.account.uid,
        service_gid=request.account.gid,
        base_id=request.base_id,
        release_id=request.release_id,
        runtime_revision=release.runtime_revision,
        evidence_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _require_success_output(completed: ManagedProcessResult) -> None:
    failed = False
    try:
        require_success_output(completed)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed:
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)


def _run_probe_process(argv: tuple[str, ...]) -> ManagedProcessResult:
    failed = False
    result: ManagedProcessResult | None = None
    try:
        result = run_probe_process(argv, runner=run_managed_process)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)
    return result


def _require_linux_root() -> None:
    if (
        os.name != "posix"
        or not sys.platform.startswith("linux")
        or not hasattr(os, "geteuid")
        or os.geteuid() != 0
    ):
        raise RuntimeTargetUserProbeError(_ERROR_MESSAGE)


def _default_ports() -> _ProbePorts:
    from codev_platform.core.runtime_models import sha256_file
    from codev_platform.runtime_base import verify_base_locked
    from codev_platform.runtime_build import (
        read_release_base_id_locked,
        verify_release_locked,
    )
    from codev_platform.runtime_storage import id_lock

    return _ProbePorts(
        id_lock=id_lock,
        read_release_base_id_locked=read_release_base_id_locked,
        verify_base_locked=verify_base_locked,
        verify_release_locked=verify_release_locked,
        sha256_file=sha256_file,
        build_service_process_argv=build_service_process_argv,
        run_probe=_run_probe_process,
    )


__all__ = [
    "RuntimeTargetUserProbeError",
    "RuntimeTargetUserProof",
    "probe_runtime_target_user",
]
