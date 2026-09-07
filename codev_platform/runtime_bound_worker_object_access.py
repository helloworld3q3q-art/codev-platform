"""root-fd worker 使用的对象访问定型适配器。"""

from __future__ import annotations

from pathlib import Path

from codev_platform.runtime_fd_tree import (
    CanonicalizeMode,
    RuntimeFdTreeError,
    VerifyOnly,
    walk_runtime_tree_from_cwd,
)
from codev_platform.runtime_object_access import (
    RUNTIME_OBJECT_MARKER_POLICY,
    RUNTIME_OBJECT_MODE_POLICY,
    RuntimeObjectAccessError,
)


def seal_object_access_from_cwd(root: Path) -> None:
    """在 worker 当前目录锚定的对象根内定型访问模式。"""
    _walk(
        root,
        CanonicalizeMode(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        ),
    )


def verify_object_access_from_cwd(root: Path) -> None:
    """在 worker 当前目录锚定的对象根内复验访问模式。"""
    _walk(
        root,
        VerifyOnly(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        ),
    )


def _walk(root: Path, operation: VerifyOnly | CanonicalizeMode) -> None:
    try:
        walk_runtime_tree_from_cwd(Path(root), operation)
    except RuntimeFdTreeError as error:
        raise RuntimeObjectAccessError(str(error)) from None


__all__ = ["seal_object_access_from_cwd", "verify_object_access_from_cwd"]
