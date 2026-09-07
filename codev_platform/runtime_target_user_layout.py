"""目标服务用户探针的不可变布局与快照契约。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import stat

from codev_platform.core.runtime_models import (
    RUNTIME_ACCESS_PROFILE,
    BaseMetadata,
    ReleaseMetadata,
    require_runtime_revision,
    require_sha256,
)
from codev_platform.runtime_service_process import ServiceAccount


class RuntimeTargetUserLayoutError(RuntimeError):
    """探针对象、路径或快照不满足不可变布局契约。"""


@dataclass(frozen=True, slots=True)
class TargetProbeRequest:
    """完成词法归一化后的目标用户探针请求。"""

    root: Path
    account: ServiceAccount
    base_id: str
    release_id: str


@dataclass(frozen=True, slots=True)
class TargetProbeLayout:
    """已解析并验证的基座与薄发布布局。"""

    base_root: Path
    release_root: Path
    python: Path
    resolved_python: Path
    prefix: Path
    app_purelib: Path
    base_purelib: Path
    base_link: Path
    base_pth: Path
    base_metadata: Path
    release_metadata: Path


@dataclass(frozen=True, slots=True)
class TargetProbeEntryIdentity:
    """一次 lstat 所见的关键目录项身份。"""

    path: Path
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int
    link_target: str | None


@dataclass(frozen=True, slots=True)
class TargetProbeSnapshot:
    """动态执行前后的不可变对象证据。"""

    base_metadata_sha256: str
    base_pth_sha256: str
    release_metadata_sha256: str
    entries: tuple[TargetProbeEntryIdentity, ...]


def validate_probe_request(
    root: Path,
    account: ServiceAccount,
    base_id: str,
    release_id: str,
) -> TargetProbeRequest:
    """在创建端口或获取锁前完成纯输入验证。"""
    if type(account) is not ServiceAccount:
        raise RuntimeTargetUserLayoutError("目标用户探针请求无效")
    return TargetProbeRequest(
        root=_normalize_runtime_root(root),
        account=account,
        base_id=require_nonzero_sha256(base_id),
        release_id=require_nonzero_sha256(release_id),
    )


def require_probe_metadata(
    request: TargetProbeRequest,
    base: BaseMetadata,
    release: ReleaseMetadata,
) -> None:
    """验证基座、发布及请求之间的完整身份绑定。"""
    requirements_sha = require_nonzero_sha256(base.requirements_sha256)
    try:
        runtime_revision = require_runtime_revision(release.runtime_revision, git_only=True)
    except (TypeError, ValueError):
        raise RuntimeTargetUserLayoutError("目标用户探针元数据无效") from None
    if (
        base.schema_version != 3
        or base.access_profile != RUNTIME_ACCESS_PROFILE
        or base.base_id != request.base_id
        or release.schema_version != 1
        or release.release_id != request.release_id
        or release.base_id != request.base_id
        or release.base_requirements_sha256 != requirements_sha
        or runtime_revision != release.runtime_revision
    ):
        raise RuntimeTargetUserLayoutError("目标用户探针元数据无效")
    require_nonzero_sha256(release.wheel_sha256)


def derive_probe_layout(
    request: TargetProbeRequest,
    base: BaseMetadata,
    release: ReleaseMetadata,
) -> TargetProbeLayout:
    """把已验证元数据收敛为不跟随非预期链接的固定布局。"""
    base_root = _plain_directory(request.root / "bases" / request.base_id)
    release_root = _plain_directory(request.root / "releases" / request.release_id)
    python_relative = _relative_path(release.python_relative)
    if len(python_relative.parts) < 2:
        raise RuntimeTargetUserLayoutError("目标用户探针解释器布局无效")
    prefix = _plain_directory(release_root / python_relative.parts[0])
    python = release_root.joinpath(*python_relative.parts)
    if not python.is_file() or python.parent.resolve(strict=True) != python.parent:
        raise RuntimeTargetUserLayoutError("目标用户探针解释器布局无效")
    resolved_python = python.resolve(strict=True)
    if not resolved_python.is_file():
        raise RuntimeTargetUserLayoutError("目标用户探针解释器布局无效")
    app_purelib = _plain_directory(
        release_root.joinpath(*_relative_path(release.purelib_relative).parts)
    )
    base_purelib = _plain_directory(
        base_root.joinpath(*_relative_path(base.purelib_relative).parts)
    )
    if not app_purelib.is_relative_to(prefix):
        raise RuntimeTargetUserLayoutError("目标用户探针模块布局无效")
    base_link = release_root.joinpath(*_relative_path(release.base_link_relative).parts)
    if not base_link.is_symlink() or base_link.resolve(strict=True) != base_root:
        raise RuntimeTargetUserLayoutError("目标用户探针基座链接无效")
    base_pth = _plain_file(release_root.joinpath(*_relative_path(release.base_pth_relative).parts))
    if base_pth.parent != app_purelib:
        raise RuntimeTargetUserLayoutError("目标用户探针路径注入无效")
    return TargetProbeLayout(
        base_root=base_root,
        release_root=release_root,
        python=python,
        resolved_python=resolved_python,
        prefix=prefix,
        app_purelib=app_purelib,
        base_purelib=base_purelib,
        base_link=base_link,
        base_pth=base_pth,
        base_metadata=_plain_file(base_root / "base.json"),
        release_metadata=_plain_file(release_root / "release.json"),
    )


def probe_environment(layout: TargetProbeLayout) -> dict[str, str]:
    """返回固定、最小且可逐项证明的探针环境。"""
    return {
        "CODEV_PLATFORM_RELEASE_FILE": os.fspath(layout.release_metadata),
        "HOME": "/",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
    }


def snapshot_probe_layout(
    layout: TargetProbeLayout,
    sha256_file: Callable[[Path], str],
) -> TargetProbeSnapshot:
    """冻结关键路径的内容摘要与目录项身份。"""
    selected = tuple(
        dict.fromkeys(
            (
                layout.base_root,
                layout.release_root,
                layout.python,
                layout.resolved_python,
                layout.prefix,
                layout.app_purelib,
                layout.base_purelib,
                layout.base_link,
                layout.base_pth,
                layout.base_metadata,
                layout.release_metadata,
            )
        )
    )
    return TargetProbeSnapshot(
        base_metadata_sha256=require_nonzero_sha256(sha256_file(layout.base_metadata)),
        base_pth_sha256=require_nonzero_sha256(sha256_file(layout.base_pth)),
        release_metadata_sha256=require_nonzero_sha256(sha256_file(layout.release_metadata)),
        entries=tuple(_entry_identity(path) for path in selected),
    )


def require_probe_unchanged(
    before: TargetProbeSnapshot,
    layout: TargetProbeLayout,
    sha256_file: Callable[[Path], str],
) -> None:
    """动态探针返回后重新快照并拒绝任何漂移。"""
    if snapshot_probe_layout(layout, sha256_file) != before:
        raise RuntimeTargetUserLayoutError("目标用户探针执行期间布局漂移")


def require_nonzero_sha256(value: object) -> str:
    """使用运行时模型真值校验内容身份。"""
    try:
        return require_sha256(value, field="runtime_object_id")
    except (TypeError, ValueError):
        raise RuntimeTargetUserLayoutError("目标用户探针对象身份无效") from None


def _entry_identity(path: Path) -> TargetProbeEntryIdentity:
    metadata = path.lstat()
    target = os.readlink(path) if stat.S_ISLNK(metadata.st_mode) else None
    return TargetProbeEntryIdentity(
        path=path,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        links=metadata.st_nlink,
        size=metadata.st_size,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
        link_target=target,
    )


def _plain_directory(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir() or path.resolve(strict=True) != path:
        raise RuntimeTargetUserLayoutError("目标用户探针目录无效")
    return path


def _plain_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True) != path:
        raise RuntimeTargetUserLayoutError("目标用户探针文件无效")
    return path


def _relative_path(value: object) -> PurePosixPath:
    if type(value) is not str or not value:
        raise RuntimeTargetUserLayoutError("目标用户探针相对路径无效")
    selected = PurePosixPath(value)
    if selected.is_absolute() or ".." in selected.parts or selected.as_posix() != value:
        raise RuntimeTargetUserLayoutError("目标用户探针相对路径无效")
    return selected


def _normalize_runtime_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise RuntimeTargetUserLayoutError("目标用户探针根目录无效")
    normalized = Path(os.path.abspath(os.fspath(candidate)))
    resolved = candidate.resolve(strict=True)
    if candidate != normalized or resolved != normalized or normalized == Path(normalized.anchor):
        raise RuntimeTargetUserLayoutError("目标用户探针根目录无效")
    return normalized


__all__ = [
    "RuntimeTargetUserLayoutError",
    "TargetProbeLayout",
    "TargetProbeRequest",
    "TargetProbeSnapshot",
    "derive_probe_layout",
    "probe_environment",
    "require_nonzero_sha256",
    "require_probe_metadata",
    "require_probe_unchanged",
    "snapshot_probe_layout",
    "validate_probe_request",
]
