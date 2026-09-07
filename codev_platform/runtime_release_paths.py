"""薄 release 的路径表示、元数据路径和链接身份契约。"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from codev_platform.core.runtime_models import require_sha256
from codev_platform.runtime_errors import RuntimeBuildError


_RELEASE_ID = re.compile(r"[0-9a-f]{64}\Z")


def require_release_id(value: object) -> str:
    """验证内容寻址 release ID。"""
    if type(value) is not str or _RELEASE_ID.fullmatch(value) is None or set(value) == {"0"}:
        raise RuntimeBuildError("release ID 无效")
    return value


def managed_release_path(root: Path, relative: str, label: str) -> Path:
    """从 release 元数据派生不逃逸的受管路径。"""
    component = Path(relative)
    if component.is_absolute() or not component.parts or ".." in component.parts:
        raise RuntimeBuildError(f"薄 release {label}路径越界")
    candidate = root / component
    try:
        candidate.relative_to(root)
    except ValueError:
        raise RuntimeBuildError(f"薄 release {label}路径越界") from None
    return candidate


def read_release_stage(marker: Path) -> str:
    """读取完成前唯一允许的 release 标记阶段。"""
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeBuildError("薄 release 完成标记无效")
    try:
        value = marker.read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise RuntimeBuildError("薄 release 完成标记无效") from None
    if value != "after_release_json\n":
        raise RuntimeBuildError("薄 release 完成阶段无效")
    return value.removesuffix("\n")


def relative_posix_components(relative: str, error_message: str) -> tuple[str, ...]:
    """拒绝运行时 metadata 或子进程输出中的路径逃逸组件。"""
    if type(relative) is not str or not relative or "\\" in relative or "\x00" in relative:
        raise RuntimeBuildError(error_message)
    components = tuple(relative.split("/"))
    if any(component in {"", ".", ".."} for component in components):
        raise RuntimeBuildError(error_message)
    return components


def managed_directory(root: Path, relative: str, *, error_message: str) -> Path:
    """在同一表示域内派生没有符号链接组件的目录。"""
    current = root
    for component in relative_posix_components(relative, error_message):
        current /= component
        if current.is_symlink() or not current.is_dir():
            raise RuntimeBuildError(error_message)
    return current


def absolute_reference(path: Path, *, error_message: str) -> Path:
    """验证仅用于元数据或 `.pth` 序列化的规范绝对路径。"""
    reference = Path(path)
    if not reference.is_absolute() or reference != Path(os.path.abspath(reference)):
        raise RuntimeBuildError(error_message)
    return reference


def base_purelib_reference(
    runtime_root: Path,
    base_id: str,
    purelib_relative: str,
) -> Path:
    """从已验证的绝对 runtime 引用词法派生 `.pth` 的唯一文本。"""
    root = absolute_reference(runtime_root, error_message="运行时根发布引用不可用")
    components = relative_posix_components(
        purelib_relative,
        "依赖基座 purelib 发布引用不可用",
    )
    return root / "bases" / base_id / Path(*components)


def cwd_base_purelib_reference(base_id: str, purelib_relative: str) -> Path:
    """派生仅供 root-fd worker 动态阶段使用的稳定 base `.pth` 引用。"""
    if os.name != "posix":
        raise RuntimeBuildError("cwd 基座 purelib 发布引用仅支持 POSIX")
    try:
        normalized_base_id = require_sha256(base_id, field="base_id")
    except ValueError:
        raise RuntimeBuildError("依赖基座 ID 无效") from None
    components = relative_posix_components(
        purelib_relative,
        "依赖基座 purelib 发布引用不可用",
    )
    return Path("/proc/self/cwd") / "bases" / normalized_base_id / Path(*components)


def same_directory(left: Path, right: Path) -> bool:
    """以 inode 身份比较目录，不把 worker 相对路径转为命名绝对路径。"""
    try:
        left_metadata = left.stat()
        right_metadata = right.stat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(left_metadata.st_mode)
        and stat.S_ISDIR(right_metadata.st_mode)
        and left_metadata.st_dev == right_metadata.st_dev
        and left_metadata.st_ino == right_metadata.st_ino
    )


__all__ = [
    "absolute_reference",
    "base_purelib_reference",
    "cwd_base_purelib_reference",
    "managed_directory",
    "managed_release_path",
    "read_release_stage",
    "relative_posix_components",
    "require_release_id",
    "same_directory",
]
