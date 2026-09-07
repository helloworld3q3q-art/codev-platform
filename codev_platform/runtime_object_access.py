"""运行时内容对象的规范访问模式定型与复验。"""

from __future__ import annotations

import stat
from pathlib import Path

from codev_platform.runtime_fd_tree import (
    CanonicalizeMode,
    RuntimeFdModePolicy,
    RuntimeFdTreeError,
    RuntimeRootMarkerPolicy,
    VerifyOnly,
    walk_runtime_tree,
)


RUNTIME_OBJECT_MODE_POLICY = RuntimeFdModePolicy(
    directory_mode=0o750,
    regular_mode=0o640,
    executable_mode=0o750,
    executable_mask=stat.S_IXUSR,
)
RUNTIME_OBJECT_MARKER_POLICY = RuntimeRootMarkerPolicy(
    name=".incomplete",
    trusted_contents=(
        b"after_marker\n",
        b"after_venv\n",
        b"after_install\n",
        b"after_base_json\n",
        b"after_release_json\n",
    ),
    max_bytes=64,
)


class RuntimeObjectAccessError(RuntimeError):
    """运行时对象树不满足访问策略。"""


def seal_runtime_object_access(root: Path) -> None:
    """定型可信未完成对象树的访问模式；不得用于修改已完成对象。"""
    _walk(
        root,
        CanonicalizeMode(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        ),
    )


def verify_runtime_object_access(root: Path) -> None:
    """由 Linux root 只读复验对象树的所有权、边界和规范访问模式。"""
    _walk(
        root,
        VerifyOnly(
            mode_policy=RUNTIME_OBJECT_MODE_POLICY,
            marker_policy=RUNTIME_OBJECT_MARKER_POLICY,
        ),
    )


def canonical_runtime_object_mode(mode: int) -> int:
    """定型目录 mode；普通文件只继承 owner 既有执行意图。"""
    try:
        return RUNTIME_OBJECT_MODE_POLICY.canonical_mode(mode)
    except RuntimeFdTreeError as error:
        raise RuntimeObjectAccessError(str(error)) from None


def _walk(root: Path, operation: VerifyOnly | CanonicalizeMode) -> None:
    try:
        walk_runtime_tree(Path(root), operation)
    except RuntimeFdTreeError as error:
        raise RuntimeObjectAccessError(str(error)) from None


__all__ = [
    "RUNTIME_OBJECT_MARKER_POLICY",
    "RUNTIME_OBJECT_MODE_POLICY",
    "RuntimeObjectAccessError",
    "canonical_runtime_object_mode",
    "seal_runtime_object_access",
    "verify_runtime_object_access",
]
