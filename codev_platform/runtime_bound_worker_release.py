"""root-fd child 内部执行的薄 release 预计算与构建操作。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from codev_platform.core.runtime_models import (
    canonical_json_bytes,
    compute_release_id,
    require_sha256,
    sha256_file,
)
from codev_platform.runtime_bound_worker_object_access import (
    seal_object_access_from_cwd,
    verify_object_access_from_cwd,
)
from codev_platform.runtime_bound_worker_base import verify_base_locked_from_cwd
from codev_platform.runtime_cwd_layout import (
    RuntimeCwdLayoutError,
    verify_runtime_layout_from_cwd,
)
from codev_platform.runtime_errors import RuntimeBuildError
from codev_platform.runtime_object_access import RuntimeObjectAccessError
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBinding


_REQUEST_FIELDS = frozenset({"base_id", "candidate_file", "runtime_root", "wheel"})
_STAGE_FIELDS = _REQUEST_FIELDS | frozenset({"base_metadata_sha256", "release_id"})
_READ_FIELDS = frozenset({"release_id", "runtime_root"})
_VERIFY_FIELDS = _READ_FIELDS | frozenset({"base_id"})


def prepare_release(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在父进程持有共享基座锁时生成最终 release 身份。"""
    runtime_root, wheel, candidate_file, base_id = _decode_request(payload, _REQUEST_FIELDS)
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor) as bound_root:
        _verify_runtime_layout()
        return _prepared_value(Path("."), wheel, candidate_file, base_id, bound_root)


def stage_release(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在父进程同时持有共享基座锁和排他 release 锁时构建最终对象。"""
    runtime_root, wheel, candidate_file, base_id = _decode_request(payload, _STAGE_FIELDS)
    expected_release_id = _require_sha256(payload["release_id"], "release ID")
    expected_base_sha = _require_sha256(payload["base_metadata_sha256"], "基座元数据摘要")
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor) as bound_root:
        root = Path(".")
        _verify_runtime_layout()
        prepared = _prepared_value(root, wheel, candidate_file, base_id, bound_root)
        if (
            prepared["release_id"] != expected_release_id
            or prepared["base_metadata_sha256"] != expected_base_sha
        ):
            raise RuntimeError("薄 release worker 锁定后基座或候选身份发生漂移")
        from codev_platform import runtime_build

        candidate, wheel_path, wheel_bytes = runtime_build._read_candidate_bundle(
            wheel,
            candidate_file,
        )
        base = verify_base_locked_from_cwd(base_id, bound_root)
        metadata = runtime_build._stage_release_locked(
            root,
            bound_root,
            candidate,
            wheel_path,
            wheel_bytes,
            base,
            expected_base_sha,
            expected_release_id,
            runtime_root_reference=runtime_root,
            finalize_base_pth=lambda path, content: _finalize_base_pth(
                bound_root,
                path,
                content,
            ),
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
            verify_object_access=_verify_release_object_access,
        )
        return json.loads(canonical_json_bytes(metadata))


def read_release_base_id(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在父进程持有共享 release 锁时读取并复验其绑定的基座 ID。"""
    runtime_root, release_id = _decode_read_request(payload)
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor):
        root = Path(".")
        _verify_runtime_layout()
        from codev_platform import runtime_build

        directory = root / "releases" / release_id
        _verify_release_object_access(directory)
        metadata = runtime_build._read_release_metadata_locked(root, release_id)
        if metadata.release_id != release_id:
            raise RuntimeError("薄 release worker 元数据身份不一致")
        return {"base_id": metadata.base_id}


def verify_release(payload: dict[str, object], root_descriptor: int) -> dict[str, object]:
    """在父进程同时持有共享 release/base 锁时完成完整静态复验。"""
    runtime_root, release_id, base_id = _decode_verify_request(payload)
    with RuntimeRootBinding(
        runtime_root,
        os.geteuid(),
    ).bind_inherited_descriptor(root_descriptor) as bound_root:
        root = Path(".")
        _verify_runtime_layout()
        from codev_platform import runtime_build

        base = verify_base_locked_from_cwd(base_id, bound_root)
        metadata = runtime_build._verify_release_directory(
            root,
            release_id,
            base_locked=True,
            verified_base=base,
            object_access_verified=True,
            runtime_root_reference=runtime_root,
        )
        return json.loads(canonical_json_bytes(metadata))


def _verify_release_object_access(root: Path) -> None:
    """把通用对象访问失败收敛为 release 内核可隔离的领域错误。"""
    try:
        verify_object_access_from_cwd(root)
    except RuntimeObjectAccessError:
        raise RuntimeBuildError("薄 release 对象访问模式无法复验") from None


def _verify_runtime_layout() -> None:
    """将 cwd 直接布局失败收敛为薄 release 的构建边界错误。"""
    try:
        verify_runtime_layout_from_cwd()
    except RuntimeCwdLayoutError as error:
        raise RuntimeBuildError(str(error)) from None


def _finalize_base_pth(
    bound_root: BoundRuntimeRoot,
    relative_path: Path,
    content: bytes,
) -> None:
    """经同一 root binding 原子切换动态 `.pth` 为激活期最终引用。"""
    if relative_path.is_absolute():
        raise RuntimeBuildError("薄 release 最终基座 pth 路径必须相对当前根")
    from codev_platform.runtime_managed_file import (
        ManagedFileError,
        ManagedFilePolicy,
        write_managed_bytes_atomic_at,
    )

    try:
        write_managed_bytes_atomic_at(
            bound_root.path / relative_path,
            content,
            root=bound_root,
            policy=ManagedFilePolicy(mode=0o640, require_uid=bound_root.owner_uid, max_bytes=4096),
        )
    except ManagedFileError as error:
        raise RuntimeBuildError("薄 release 最终基座 pth 无法原子发布") from error


def _prepared_value(
    root: Path,
    wheel: Path,
    candidate_file: Path,
    base_id: str,
    bound_root: BoundRuntimeRoot,
) -> dict[str, str]:
    from codev_platform import runtime_build

    candidate, _wheel_path, _wheel_bytes = runtime_build._read_candidate_bundle(
        wheel,
        candidate_file,
    )
    base = verify_base_locked_from_cwd(base_id, bound_root)
    base_metadata_sha = sha256_file(root / "bases" / base.base_id / "base.json")
    return {
        "base_metadata_sha256": base_metadata_sha,
        "release_id": compute_release_id(
            candidate.runtime_revision,
            candidate.wheel_sha256,
            base.base_id,
            base_metadata_sha,
        ),
    }


def _decode_request(
    payload: dict[str, object],
    fields: frozenset[str],
) -> tuple[Path, Path, Path, str]:
    if set(payload) != fields:
        raise ValueError("薄 release worker 请求字段无效")
    return (
        _absolute_path(payload["runtime_root"], "运行时根"),
        _absolute_path(payload["wheel"], "候选 wheel"),
        _absolute_path(payload["candidate_file"], "候选元数据"),
        _require_sha256(payload["base_id"], "基座 ID"),
    )


def _decode_read_request(payload: dict[str, object]) -> tuple[Path, str]:
    if set(payload) != _READ_FIELDS:
        raise ValueError("薄 release worker 读取请求字段无效")
    return (
        _absolute_path(payload["runtime_root"], "运行时根"),
        _require_sha256(payload["release_id"], "release ID"),
    )


def _decode_verify_request(payload: dict[str, object]) -> tuple[Path, str, str]:
    if set(payload) != _VERIFY_FIELDS:
        raise ValueError("薄 release worker 复验请求字段无效")
    runtime_root, release_id = _decode_read_request(
        {"release_id": payload["release_id"], "runtime_root": payload["runtime_root"]},
    )
    return runtime_root, release_id, _require_sha256(payload["base_id"], "基座 ID")


def _absolute_path(value: object, label: str) -> Path:
    if type(value) is not str:
        raise ValueError(f"薄 release worker {label}路径无效")
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.abspath(path)):
        raise ValueError(f"薄 release worker {label}路径必须规范绝对")
    return path


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"薄 release worker {label}无效")
    return value


def _require_sha256(value: object, label: str) -> str:
    try:
        return require_sha256(value, field=label)
    except ValueError as error:
        raise ValueError(f"薄 release worker {label}无效") from error


def _isolate_under_parent_lock(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> Path | None:
    """静态 worker 仅复用父进程仍持有的对象锁，不接收锁 descriptor。"""
    from codev_platform.runtime_isolation import _isolate_bound_root

    return _isolate_bound_root(
        root,
        kind,
        object_id,
        completed_failure_stage=completed_failure_stage,
    )
