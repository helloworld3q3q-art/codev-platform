"""root-fd worker 当前目录运行时布局的安全门禁回归。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform.runtime_cwd_layout import (
    RuntimeCwdLayoutError,
    verify_runtime_layout_from_cwd,
)


_ROOT_POSIX = os.name == "posix" and os.geteuid() == 0


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    (root / "bases").mkdir(parents=True, mode=0o755)
    (root / "releases").mkdir(mode=0o755)
    return root


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 布局")
def testcwd运行时布局接受可信直接目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """受绑定根内的两类受管直接目录必须可被同一 cwd 门禁证明。"""
    root = _runtime_root(tmp_path)
    monkeypatch.chdir(root)

    verify_runtime_layout_from_cwd()


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 布局")
@pytest.mark.parametrize("name", ("bases", "releases"))
def testcwd运行时布局拒绝受管一级目录链接(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """`bases` 或 `releases` 不得把 worker I/O 导向 runtime 根外。"""
    root = _runtime_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    (root / name).rmdir()
    (root / name).symlink_to(outside, target_is_directory=True)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeCwdLayoutError, match=name):
        verify_runtime_layout_from_cwd()


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 布局")
def testcwd运行时布局拒绝受管一级目录被非所有者写入(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """即使不是链接，组或其他用户可写的受管目录也必须闭锁。"""
    root = _runtime_root(tmp_path)
    os.chmod(root / "releases", 0o775)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeCwdLayoutError, match="releases"):
        verify_runtime_layout_from_cwd()


@pytest.mark.skipif(not _ROOT_POSIX, reason="仅由 root 的 POSIX worker 验证 cwd 布局")
def testcwd运行时布局拒绝受管一级目录非root所有(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """直接目录的属主同样属于 root-fd worker 的固定布局契约。"""
    root = _runtime_root(tmp_path)
    os.chown(root / "bases", 1, -1)
    monkeypatch.chdir(root)

    with pytest.raises(RuntimeCwdLayoutError, match="bases"):
        verify_runtime_layout_from_cwd()
