"""证明当前进程属于完整、未逃逸且摘要一致的版本运行时。"""
from __future__ import annotations

import sys
from pathlib import Path

from .runtime_models import (
    BaseMetadata,
    ReleaseMetadata,
    RuntimeIdentity,
    compute_base_id,
    compute_release_id,
    current_abi,
    read_base_metadata,
    read_release_metadata,
    sha256_file,
)


class ReleaseIdentityProofError(RuntimeError):
    """版本身份或受管路径证明失败。"""


def _fail(message: str) -> ReleaseIdentityProofError:
    return ReleaseIdentityProofError(message)


def _logical_executable(path: Path) -> Path:
    """解析父目录但保留 venv Python 最后一跳符号链接。"""
    return path.parent.resolve(strict=True) / path.name


def _release_layout(release_path: Path) -> tuple[Path, ReleaseMetadata]:
    try:
        release_file = release_path.expanduser().resolve(strict=True)
    except OSError:
        raise _fail("声明的版本元数据不可用") from None
    release_dir = release_file.parent
    if release_file.name != "release.json" or (release_dir / ".incomplete").exists():
        raise _fail("声明的版本元数据无效或尚未完成")
    release = read_release_metadata(release_file)
    if release_dir.name != release.release_id or release_dir.parent.name != "releases":
        raise _fail("版本目录与版本身份不一致")
    return release_dir, release


def _verify_interpreter(release_dir: Path, python_relative: str) -> tuple[Path, Path]:
    relative = Path(python_relative)
    if len(relative.parts) < 2:
        raise _fail("版本解释器相对路径无效")
    prefix_path = release_dir / relative.parts[0]
    if prefix_path.is_symlink():
        raise _fail("版本环境目录不能是符号链接")
    expected_prefix = prefix_path.resolve(strict=True)
    declared_python = _logical_executable(release_dir / relative)
    actual_python = _logical_executable(Path(sys.executable))
    actual_prefix = Path(sys.prefix).resolve(strict=True)
    if (
        actual_python != declared_python
        or actual_prefix != expected_prefix
        or not expected_prefix.is_relative_to(release_dir)
        or not declared_python.is_relative_to(expected_prefix)
    ):
        raise _fail("当前解释器与声明版本不一致")
    return Path(sys.executable).resolve(strict=True), actual_prefix


def _expected_base(release_dir: Path, base_id: str) -> Path:
    runtime_root = release_dir.parent.parent.resolve(strict=True)
    bases_path = runtime_root / "bases"
    if bases_path.is_symlink():
        raise _fail("基座根目录不能是符号链接")
    bases_root = bases_path.resolve(strict=True)
    if bases_root.parent != runtime_root:
        raise _fail("基座根目录逃逸运行时根目录")
    expected_path = bases_root / base_id
    if expected_path.is_symlink():
        raise _fail("规范基座目录不能是符号链接")
    expected = expected_path.resolve(strict=True)
    if expected.parent != bases_root or (expected / ".incomplete").exists():
        raise _fail("规范基座目录无效或尚未完成")
    return expected


def _verify_base(release_dir: Path, release: ReleaseMetadata) -> BaseMetadata:
    expected = _expected_base(release_dir, release.base_id)
    base_link = release_dir / release.base_link_relative
    if not base_link.is_symlink() or base_link.resolve(strict=True) != expected:
        raise _fail("版本基座链接与声明身份不一致")
    base_file = expected / "base.json"
    if base_file.is_symlink() or not base_file.is_file():
        raise _fail("基座元数据文件无效")
    base_sha = sha256_file(base_file)
    base = read_base_metadata(base_file)
    lock_path = expected / base.lock_relative
    if lock_path.is_symlink():
        raise _fail("requirements 锁不能是符号链接")
    lock_file = lock_path.resolve(strict=True)
    if not lock_file.is_file() or not lock_file.is_relative_to(expected):
        raise _fail("requirements 锁逃逸规范基座")
    if (
        sha256_file(lock_file) != base.requirements_sha256
        or base_sha != release.base_metadata_sha256
        or base.base_id != release.base_id
        or base.requirements_sha256 != release.base_requirements_sha256
        or compute_base_id(base.requirements_sha256, base.abi) != base.base_id
        or base.abi != current_abi()
    ):
        raise _fail("依赖基座身份不一致")
    return base


def release_identity(release_path: Path) -> RuntimeIdentity:
    """验证版本链并返回当前进程的不可变身份。"""
    release_dir, release = _release_layout(release_path)
    actual_python, actual_prefix = _verify_interpreter(release_dir, release.python_relative)
    _verify_base(release_dir, release)
    expected_release_id = compute_release_id(
        release.runtime_revision,
        release.wheel_sha256,
        release.base_id,
        release.base_metadata_sha256,
    )
    if expected_release_id != release.release_id:
        raise _fail("版本身份摘要不一致")
    return RuntimeIdentity(
        mode="release",
        runtime_revision=release.runtime_revision,
        release_id=release.release_id,
        wheel_sha256=release.wheel_sha256,
        base_id=release.base_id,
        base_requirements_sha256=release.base_requirements_sha256,
        interpreter_realpath=str(actual_python),
        environment_prefix=str(actual_prefix),
        source_root=None,
    )
