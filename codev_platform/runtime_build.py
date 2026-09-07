"""从干净 Git 提交构建 wheel，并产出内容寻址的薄 release。"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from codev_platform.core.runtime_models import (
    BaseMetadata,
    ReleaseCandidate,
    ReleaseMetadata,
    compute_release_id,
    read_release_metadata,
    sha256_file,
    write_release_metadata_atomic,
)
from codev_platform.core.runtime_metadata_io import decode_typed_bytes
from codev_platform.runtime_candidate import build_candidate, compute_candidate_id
from codev_platform.runtime_build_bound import (
    stage_bound_release as _run_bound_stage_release,
    verify_bound_release as _run_bound_verify_release,
)
from codev_platform.runtime_deadline import runtime_operation_timeout
from codev_platform.runtime_errors import RuntimeBuildError, RuntimeIdCollisionError
from codev_platform.runtime_release_environment import (
    stage_environment as _stage_environment,
    verify_environment as _verify_environment,
    write_bytes_exclusive as _write_bytes_exclusive,
    write_stage as _write_stage,
)
from codev_platform.runtime_wheel import (
    RuntimeWheelError,
    inspect_application_wheel as _inspect_application_wheel,
)
from codev_platform.runtime_release_paths import (
    base_purelib_reference as _base_purelib_reference,
    managed_directory as _managed_directory,
    managed_release_path as _managed_release_path,
    read_release_stage as _read_stage,
    require_release_id as _require_release_id,
    same_directory as _same_directory,
)


@runtime_operation_timeout(1800.0)
def stage_release(
    root: Path,
    wheel: Path,
    candidate_file: Path,
    base_id: str,
) -> ReleaseMetadata:
    """在最终内容寻址路径直接构建薄 release，完成前保持 .incomplete。"""
    _require_privileged_builder()
    runtime_root = Path(os.path.abspath(os.fspath(root)))
    _candidate, wheel_path, _wheel_bytes = _read_candidate_bundle(wheel, candidate_file)
    return _run_bound_stage_release(
        runtime_root,
        wheel_path,
        Path(candidate_file).expanduser().absolute(),
        base_id,
    )


def _stage_release_direct(
    root: Path,
    wheel: Path,
    candidate_file: Path,
    base_id: str,
) -> ReleaseMetadata:
    """仅供受控单元测试替换 worker facade 时复用既有构建内核。"""
    runtime_root = _require_runtime_root(root)
    candidate, wheel_path, wheel_bytes = _read_candidate_bundle(wheel, candidate_file)
    base = _verify_base(runtime_root, base_id)
    base_dir = runtime_root / "bases" / base.base_id
    base_metadata_sha = sha256_file(base_dir / "base.json")
    release_id = compute_release_id(
        candidate.runtime_revision,
        candidate.wheel_sha256,
        base.base_id,
        base_metadata_sha,
    )
    with _id_lock(runtime_root, "release", release_id, shared=False) as bound_root:
        with _id_lock(runtime_root, "base", base.base_id, shared=True):
            locked_base = _verify_base_locked(runtime_root, base.base_id)
            locked_base_dir = runtime_root / "bases" / locked_base.base_id
            locked_base_sha = sha256_file(locked_base_dir / "base.json")
            locked_release_id = compute_release_id(
                candidate.runtime_revision,
                candidate.wheel_sha256,
                locked_base.base_id,
                locked_base_sha,
            )
            if locked_release_id != release_id or locked_base != base:
                raise RuntimeBuildError("依赖基座在 release 锁定前发生漂移")
            return _stage_release_locked(
                runtime_root,
                bound_root,
                candidate,
                wheel_path,
                wheel_bytes,
                locked_base,
                locked_base_sha,
                release_id,
            )


def _stage_release_locked(
    runtime_root: Path,
    bound_root: object,
    candidate: ReleaseCandidate,
    wheel_path: Path,
    wheel_bytes: bytes,
    base: BaseMetadata,
    base_metadata_sha: str,
    release_id: str,
    *,
    runtime_root_reference: Path | None = None,
    finalize_base_pth: Callable[[Path, bytes], None] | None = None,
    isolate_incomplete: Callable[[object, str, str], Path | None] | None = None,
    isolate_corrupt: Callable[[object, str, str], Path | None] | None = None,
    seal_object_access: Callable[[Path], None] | None = None,
    verify_object_access: Callable[[Path], None] | None = None,
) -> ReleaseMetadata:
    _preflight_application_wheel(wheel_path)
    isolate_incomplete = _isolate_incomplete if isolate_incomplete is None else isolate_incomplete
    isolate_corrupt = _isolate_corrupt_completed if isolate_corrupt is None else isolate_corrupt
    seal_object_access = _seal_object_access if seal_object_access is None else seal_object_access
    verify_object_access = (
        _verify_object_access if verify_object_access is None else verify_object_access
    )
    reference_root = runtime_root if runtime_root_reference is None else runtime_root_reference
    release_dir = runtime_root / "releases" / release_id
    base_dir = runtime_root / "bases" / base.base_id
    if release_dir.exists() or release_dir.is_symlink():
        marker = release_dir / ".incomplete"
        metadata_file = release_dir / "release.json"
        if marker.exists() or marker.is_symlink() or not metadata_file.exists():
            isolate_incomplete(bound_root, "release", release_id)
            if release_dir.exists() or release_dir.is_symlink():
                raise RuntimeBuildError("未完成 release 隔离后仍占用最终路径")
        else:
            try:
                existing = _verify_release_directory(
                    runtime_root,
                    release_id,
                    base_locked=True,
                    verified_base=base,
                    object_access_verifier=verify_object_access,
                    runtime_root_reference=reference_root,
                )
            except RuntimeBuildError:
                isolate_corrupt(bound_root, "release", release_id)
                if release_dir.exists() or release_dir.is_symlink():
                    raise RuntimeBuildError("损坏 release 隔离后仍占用最终路径") from None
            else:
                _require_release_inputs(existing, candidate, base, base_metadata_sha)
                return existing
    release_dir.mkdir(mode=0o755)
    marker = release_dir / ".incomplete"
    _write_stage(marker, "after_marker")
    _fsync_directory(release_dir)
    _fsync_directory(release_dir.parent)
    artifacts = release_dir / "artifacts"
    artifacts.mkdir(mode=0o755)
    sealed_wheel = artifacts / wheel_path.name
    _write_bytes_exclusive(sealed_wheel, wheel_bytes, mode=0o644)
    try:
        staged = _stage_environment(
            release_dir,
            sealed_wheel,
            base_dir,
            base,
            marker,
            base_purelib_reference=_base_purelib_reference(
                reference_root,
                base.base_id,
                base.purelib_relative,
            ),
            verify_runtime_binding=getattr(bound_root, "verify_visible", None),
            finalize_base_pth=finalize_base_pth,
        )
        _write_stage(marker, "after_install")
        seal_object_access(release_dir)
        metadata = ReleaseMetadata(
            schema_version=1,
            release_id=release_id,
            runtime_revision=candidate.runtime_revision,
            wheel_sha256=candidate.wheel_sha256,
            base_id=base.base_id,
            base_requirements_sha256=base.requirements_sha256,
            base_metadata_sha256=base_metadata_sha,
            app_freeze_sha256=staged.app_freeze_sha256,
            created_at=_utc_now(),
            python_relative=staged.python_relative,
            purelib_relative=staged.purelib_relative,
            base_link_relative=staged.base_link_relative,
            base_pth_relative=staged.base_pth_relative,
        )
        write_release_metadata_atomic(release_dir / "release.json", metadata)
        _require_release_inputs(metadata, candidate, base, base_metadata_sha)
        _write_stage(marker, "after_release_json")
        seal_object_access(release_dir)
        verified = _verify_release_directory(
            runtime_root,
            release_id,
            allow_incomplete=True,
            base_locked=True,
            verified_base=base,
            object_access_verifier=verify_object_access,
            runtime_root_reference=reference_root,
        )
        _seal_durable_tree(release_dir)
        marker.unlink()
        _fsync_directory(release_dir)
        _fsync_directory(release_dir.parent)
        return verified
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeBuildError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeBuildError("薄 release 构建失败") from error


def _preflight_application_wheel(wheel: Path) -> None:
    """在创建最终 release 目录前完成 wheel 静态载荷证明。"""
    try:
        _inspect_application_wheel(wheel)
    except RuntimeWheelError as error:
        raise RuntimeBuildError(str(error)) from None


@runtime_operation_timeout(120.0)
def verify_release(root: Path, release_id: str) -> ReleaseMetadata:
    """在共享 ID 锁内静态复核正式 release，不执行目标 Python。"""
    runtime_root = Path(os.path.abspath(os.fspath(root)))
    return _run_bound_verify_release(runtime_root, _require_release_id(release_id))


def _verify_release_direct(root: Path, release_id: str) -> ReleaseMetadata:
    runtime_root = _require_runtime_root(root)
    normalized = _require_release_id(release_id)
    with _id_lock(runtime_root, "release", normalized, shared=True):
        _verify_object_access(runtime_root / "releases" / normalized)
        metadata = _read_release_metadata_locked(runtime_root, normalized)
        with _id_lock(runtime_root, "base", metadata.base_id, shared=True):
            base = _verify_base_locked(runtime_root, metadata.base_id)
            return _verify_release_directory(
                runtime_root,
                normalized,
                base_locked=True,
                verified_base=base,
                object_access_verified=True,
            )


def read_release_base_id_locked(root: Path, release_id: str) -> str:
    """读取调用方已锁定 release 的基座 ID，不执行目标环境。"""
    runtime_root = _require_runtime_root(root)
    normalized = _require_release_id(release_id)
    _verify_object_access(runtime_root / "releases" / normalized)
    metadata = _read_release_metadata_locked(runtime_root, normalized)
    if metadata.release_id != normalized:
        raise RuntimeBuildError("薄 release 元数据身份不一致")
    return metadata.base_id


def verify_release_locked(
    root: Path,
    release_id: str,
    *,
    verified_base: BaseMetadata,
) -> ReleaseMetadata:
    """复验调用方已锁定的版本，并复用同锁域内已验证的共享基座。"""
    runtime_root = _require_runtime_root(root)
    normalized = _require_release_id(release_id)
    return _verify_release_directory(
        runtime_root,
        normalized,
        base_locked=True,
        verified_base=verified_base,
    )


def _read_candidate_bundle(
    wheel: Path,
    candidate_file: Path,
) -> tuple[ReleaseCandidate, Path, bytes]:
    wheel_path = Path(wheel).expanduser().absolute()
    candidate_path = Path(candidate_file).expanduser().absolute()
    try:
        parent = candidate_path.parent.resolve(strict=True)
        parent_metadata = candidate_path.parent.lstat()
    except OSError as error:
        raise RuntimeBuildError("候选内容地址目录不可用") from error
    if (
        wheel_path.parent != candidate_path.parent
        or parent != candidate_path.parent
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or candidate_path.parent.is_symlink()
    ):
        raise RuntimeBuildError("候选 wheel 与元数据必须位于同一规范内容地址目录")
    try:
        candidate = decode_typed_bytes(
            _read_regular_snapshot(candidate_path, max_bytes=64 * 1024),
            ReleaseCandidate,
        )
    except ValueError as error:
        raise RuntimeBuildError("候选元数据不可用") from error
    expected_parent = compute_candidate_id(candidate.runtime_revision, candidate.wheel_sha256)
    if (
        parent.name != expected_parent
        or candidate_path.name != f"{candidate.wheel_name}.candidate.json"
    ):
        raise RuntimeBuildError("候选目录与元数据内容地址不一致")
    content = _read_regular_snapshot(wheel_path, max_bytes=256 * 1024 * 1024)
    if (
        wheel_path.name != candidate.wheel_name
        or hashlib.sha256(content).hexdigest() != candidate.wheel_sha256
    ):
        raise RuntimeBuildError("候选 wheel 摘要或名称不一致")
    return candidate, wheel_path, content


def _read_regular_snapshot(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeBuildError("候选普通文件不可安全打开") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise RuntimeBuildError("候选普通文件类型或大小无效")
        blocks: list[bytes] = []
        total = 0
        while block := os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total)):
            blocks.append(block)
            total += len(block)
            if total > max_bytes:
                raise RuntimeBuildError("候选普通文件超过大小限制")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or total != after.st_size
        ):
            raise RuntimeBuildError("候选普通文件读取期间发生变化")
        return b"".join(blocks)
    except OSError as error:
        raise RuntimeBuildError("候选普通文件不可安全读取") from error
    finally:
        os.close(descriptor)


def _require_runtime_root(value: Path) -> Path:
    try:
        raw = Path(value).expanduser()
        metadata = raw.lstat()
        root = raw.resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        raise RuntimeBuildError("运行时根目录不可用") from error
    if not stat.S_ISDIR(metadata.st_mode) or raw.is_symlink():
        raise RuntimeBuildError("运行时根目录不受信任")
    for name in ("bases", "releases"):
        child = root / name
        if not child.is_dir() or child.is_symlink():
            raise RuntimeBuildError(f"运行时 {name} 目录不受信任")
    if os.name == "posix" and os.geteuid() == 0:
        for path in (root, root / "bases", root / "releases"):
            item = path.stat()
            if item.st_uid != 0 or stat.S_IMODE(item.st_mode) & 0o022:
                raise RuntimeBuildError("运行时根必须由 root 封存且不可被非 root 写入")
    return root


def _verify_release_directory(
    root: Path,
    release_id: str,
    *,
    allow_incomplete: bool = False,
    base_locked: bool = False,
    verified_base: BaseMetadata | None = None,
    object_access_verified: bool = False,
    object_access_verifier: Callable[[Path], None] | None = None,
    environment_verifier: Callable[..., None] | None = None,
    runtime_root_reference: Path | None = None,
) -> ReleaseMetadata:
    normalized = _require_release_id(release_id)
    directory = root / "releases" / normalized
    if not object_access_verified:
        verifier = (
            _verify_object_access if object_access_verifier is None else object_access_verifier
        )
        verifier(directory)
    metadata = _read_release_metadata_locked(
        root,
        normalized,
        allow_incomplete=allow_incomplete,
    )
    if not base_locked or verified_base is None or verified_base.base_id != metadata.base_id:
        raise RuntimeBuildError("薄 release 预验证基座身份不一致")
    base = verified_base
    base_file = root / "bases" / base.base_id / "base.json"
    actual_base_metadata_sha = sha256_file(base_file)
    if (
        metadata.base_requirements_sha256 != base.requirements_sha256
        or metadata.base_metadata_sha256 != actual_base_metadata_sha
    ):
        raise RuntimeBuildError("薄 release 基座派生身份不一致")
    expected = compute_release_id(
        metadata.runtime_revision,
        metadata.wheel_sha256,
        metadata.base_id,
        actual_base_metadata_sha,
    )
    if expected != normalized or metadata.release_id != normalized:
        raise RuntimeBuildError("薄 release 身份摘要不一致")
    base_link = _managed_release_path(directory, metadata.base_link_relative, "基座链接")
    expected_link = os.fspath(Path("..") / ".." / "bases" / base.base_id)
    try:
        raw_link = os.readlink(base_link)
    except OSError as error:
        raise RuntimeBuildError("薄 release 基座链接不可读") from error
    if raw_link != expected_link or not _same_directory(base_link, base_file.parent):
        raise RuntimeBuildError("薄 release 基座相对链接漂移")
    artifacts_dir = directory / "artifacts"
    if artifacts_dir.is_symlink() or not artifacts_dir.is_dir():
        raise RuntimeBuildError("薄 release 制品目录不受信任")
    artifacts = tuple(artifacts_dir.glob("*.whl"))
    if (
        len(artifacts) != 1
        or artifacts[0].is_symlink()
        or not artifacts[0].is_file()
        or sha256_file(artifacts[0]) != metadata.wheel_sha256
    ):
        raise RuntimeBuildError("薄 release wheel 制品漂移")
    purelib = _managed_release_path(directory, metadata.purelib_relative, "版本 purelib")
    if purelib.is_symlink() or not purelib.is_dir():
        raise RuntimeBuildError("薄 release purelib 不受信任")
    pth = _managed_release_path(directory, metadata.base_pth_relative, "基座 .pth")
    if pth.is_symlink() or not pth.is_file() or pth.parent != purelib:
        raise RuntimeBuildError("薄 release 基座 .pth 不受信任")
    expected_purelib = _managed_directory(
        base_file.parent,
        base.purelib_relative,
        error_message="依赖基座 purelib 不受信任",
    )
    reference_root = root if runtime_root_reference is None else runtime_root_reference
    expected_purelib_reference = _base_purelib_reference(
        reference_root,
        base.base_id,
        base.purelib_relative,
    )
    try:
        lines = pth.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeBuildError("薄 release 基座 .pth 不可读") from error
    if lines != [expected_purelib_reference.as_posix()] or "import" in lines[0]:
        raise RuntimeBuildError("薄 release 基座 .pth 漂移")
    python = _managed_release_path(directory, metadata.python_relative, "版本解释器")
    if not python.is_file():
        raise RuntimeBuildError("薄 release Python 不可用")
    verifier = _verify_environment if environment_verifier is None else environment_verifier
    verifier(
        root,
        directory,
        python,
        purelib,
        expected_purelib,
        artifacts[0],
        metadata.app_freeze_sha256,
    )
    return metadata


def _read_release_metadata_locked(
    root: Path,
    release_id: str,
    *,
    allow_incomplete: bool = False,
) -> ReleaseMetadata:
    directory = root / "releases" / release_id
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeBuildError("薄 release 路径不受信任")
    marker = directory / ".incomplete"
    if marker.exists() or marker.is_symlink():
        if not allow_incomplete or _read_stage(marker) != "after_release_json":
            raise RuntimeBuildError("薄 release 尚未完成")
    metadata_file = directory / "release.json"
    if metadata_file.is_symlink() or not metadata_file.is_file():
        raise RuntimeBuildError("薄 release 元数据不受信任")
    try:
        return read_release_metadata(metadata_file)
    except ValueError as error:
        raise RuntimeBuildError("薄 release 元数据无效") from error


def _require_release_inputs(
    metadata: ReleaseMetadata,
    candidate: ReleaseCandidate,
    base: BaseMetadata,
    base_metadata_sha: str,
) -> None:
    if (
        metadata.runtime_revision != candidate.runtime_revision
        or metadata.wheel_sha256 != candidate.wheel_sha256
        or metadata.base_id != base.base_id
        or metadata.base_requirements_sha256 != base.requirements_sha256
        or metadata.base_metadata_sha256 != base_metadata_sha
    ):
        raise RuntimeIdCollisionError("相同 release ID 的身份输入发生碰撞")


def _require_privileged_builder() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeBuildError("薄 release stage 必须由 root 执行")


def _fsync_directory(path: Path) -> None:
    # Windows 无目录 fsync 语义；Linux 发布链仍保留完整持久化屏障。
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _seal_durable_tree(path: Path) -> None:
    from codev_platform.runtime_durable_tree import RuntimeDurabilityError, seal_durable_tree

    try:
        seal_durable_tree(path)
    except RuntimeDurabilityError:
        raise RuntimeBuildError("薄 release 无法形成持久化封存") from None


def _seal_object_access(release_dir: Path) -> None:
    from codev_platform.runtime_object_access import (
        RuntimeObjectAccessError,
        seal_runtime_object_access,
    )

    try:
        seal_runtime_object_access(release_dir)
    except RuntimeObjectAccessError:
        raise RuntimeBuildError("薄 release 对象访问模式无法安全定型") from None


def _verify_object_access(release_dir: Path) -> None:
    from codev_platform.runtime_object_access import (
        RuntimeObjectAccessError,
        verify_runtime_object_access,
    )

    try:
        verify_runtime_object_access(release_dir)
    except RuntimeObjectAccessError:
        raise RuntimeBuildError("薄 release 对象访问模式无法复验") from None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _verify_base(root: Path, base_id: str) -> BaseMetadata:
    from codev_platform.runtime_base import verify_base

    return verify_base(root, base_id)


def _verify_base_locked(root: Path, base_id: str) -> BaseMetadata:
    from codev_platform.runtime_base import verify_base_locked

    return verify_base_locked(root, base_id)


def _id_lock(root: Path, kind: str, object_id: str, *, shared: bool):
    from codev_platform.runtime_storage import object_lock

    return object_lock(root, kind, object_id, shared=shared)


def _isolate_incomplete(root: object, kind: str, object_id: str):
    from codev_platform.runtime_storage import isolate_incomplete_locked_at

    return isolate_incomplete_locked_at(root, kind, object_id)


def _isolate_corrupt_completed(root: object, kind: str, object_id: str):
    from codev_platform.runtime_storage import isolate_corrupt_completed_locked_at

    return isolate_corrupt_completed_locked_at(root, kind, object_id)


__all__ = [
    "RuntimeBuildError",
    "build_candidate",
    "read_release_base_id_locked",
    "stage_release",
    "verify_release",
    "verify_release_locked",
]
