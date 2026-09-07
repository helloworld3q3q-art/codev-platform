"""基座目录内的局部路径、标记和锁文件机械操作。"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from codev_platform._runtime_base_errors import RuntimeBaseIntegrityError
from codev_platform.runtime_base_environment import (
    VenvLayout as _VenvLayout,
    isolated_arguments as _isolated_arguments,
)
from codev_platform.core.runtime_models import sha256_file


def require_venv_layout(base_dir: Path, layout: object) -> None:
    if not isinstance(layout, _VenvLayout):
        raise RuntimeBaseIntegrityError("基座 venv 布局无效")
    environment = base_dir / "venv"
    for path in (layout.python, layout.purelib, layout.bin_dir):
        try:
            path.relative_to(environment)
        except ValueError:
            raise RuntimeBaseIntegrityError("基座 venv 布局逃逸最终目录") from None
    if environment.is_symlink() or not environment.is_dir():
        raise RuntimeBaseIntegrityError("基座 venv 目录无效")
    if not layout.python.is_file() or layout.python.parent != layout.bin_dir:
        raise RuntimeBaseIntegrityError("基座解释器布局无效")
    if (
        layout.purelib.is_symlink()
        or not layout.purelib.is_dir()
        or layout.bin_dir.is_symlink()
        or not layout.bin_dir.is_dir()
    ):
        raise RuntimeBaseIntegrityError("基座 venv 目录布局无效")


def managed_file(root: Path, relative: str, label: str, *, allow_symlink: bool = False) -> Path:
    path = managed_path(root, relative, label)
    if (path.is_symlink() and not allow_symlink) or not path.is_file():
        raise RuntimeBaseIntegrityError(f"{label}无效")
    return path


def managed_directory(root: Path, relative: str, label: str) -> Path:
    path = managed_path(root, relative, label)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeBaseIntegrityError(f"{label}无效")
    return path


def managed_path(root: Path, relative: str, label: str) -> Path:
    component = Path(relative)
    if component.is_absolute() or not component.parts or ".." in component.parts:
        raise RuntimeBaseIntegrityError(f"{label}逃逸基座目录")
    candidate = root / component
    try:
        candidate.relative_to(root)
    except ValueError:
        raise RuntimeBaseIntegrityError(f"{label}逃逸基座目录") from None
    return candidate


def ensure_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("基座目录不能是符号链接")
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("基座目录无效")


def copy_lock_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    shutil.copyfile(source, destination)
    if destination.is_symlink() or not destination.is_file():
        raise RuntimeBaseIntegrityError("保存的依赖锁无效")
    if sha256_file(destination) != expected_sha256:
        raise RuntimeBaseIntegrityError("保存的依赖锁摘要不一致")
    with destination.open("r+b") as stream:
        os.fsync(stream.fileno())


def write_stage(marker: Path, stage: str) -> None:
    mode = "xb" if not marker.exists() else "r+b"
    with marker.open(mode) as stream:
        stream.seek(0)
        stream.write((stage + "\n").encode("ascii"))
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise RuntimeBaseIntegrityError("基座运行路径逃逸最终目录") from None
    return relative.as_posix()


def run_isolated(
    run_python: Callable[[Path, tuple[str, ...]], bytes],
    python: Path,
    arguments: tuple[str, ...],
) -> bytes:
    return run_python(python, _isolated_arguments(arguments))
