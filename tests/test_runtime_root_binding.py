"""descriptor-bound 运行时根租约的边界测试。"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from codev_platform.runtime_root_binding import (
    RuntimeRootBinding,
    RuntimeRootBindingError,
)


_POSIX = os.name == "posix"


def _binding(root: Path, *, owner_uid: int | None = None) -> RuntimeRootBinding:
    return RuntimeRootBinding(
        root,
        owner_uid=os.geteuid() if owner_uid is None else owner_uid,
    )


def _replace_visible_root(root: Path, replacement: Path) -> Path:
    displaced = root.with_name(root.name + "-displaced")
    root.rename(displaced)
    replacement.rename(root)
    return displaced


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test同一绑定只接受自身未关闭租约且关闭后闭锁(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    binding = _binding(root)
    other = _binding(root)

    with binding.bind() as bound_root:
        binding.require_bound(bound_root)
        assert bound_root.path == root
        assert bound_root.owner_uid == os.geteuid()
        assert bound_root.relative_path(root / "attempts" / "active.json") == (
            "attempts",
            "active.json",
        )
        with pytest.raises(RuntimeRootBindingError, match="其他绑定"):
            other.require_bound(bound_root)

    with pytest.raises(RuntimeRootBindingError, match="已关闭"):
        binding.require_bound(bound_root)
    with pytest.raises(RuntimeRootBindingError, match="已关闭|永久失效"):
        bound_root.verify_visible()
    with pytest.raises(RuntimeRootBindingError, match="永久失效"):
        binding.verify()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test根目录拒绝错误属主不安全权限和符号链接(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()

    with pytest.raises(RuntimeRootBindingError, match="属主"):
        _binding(root, owner_uid=os.geteuid() + 1).verify()

    root.chmod(0o777)
    with pytest.raises(RuntimeRootBindingError, match="权限"):
        _binding(root).verify()
    root.chmod(0o755)

    linked = tmp_path / "linked-runtime"
    linked.symlink_to(root, target_is_directory=True)
    with pytest.raises(RuntimeRootBindingError, match="符号链接|安全打开"):
        _binding(linked).verify()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test冻结身份拒绝可见根inode替换并在退出时复验(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    binding = _binding(root)

    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        with binding.bind() as bound_root:
            _replace_visible_root(root, replacement)
            bound_root.verify_visible()

    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        binding.verify()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test任一漂移后同一绑定永久失效而新绑定可重新建立信任(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    binding = _binding(root)
    binding.verify()

    original = _replace_visible_root(root, replacement)
    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        binding.verify()

    root.rename(replacement)
    original.rename(root)
    with pytest.raises(RuntimeRootBindingError, match="永久失效"):
        binding.verify()
    _binding(root).verify()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test已冻结根权限漂移即使恢复同一绑定仍永久失效(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    original_mode = stat.S_IMODE(root.stat().st_mode)
    binding = _binding(root)
    binding.verify()

    root.chmod(0o777)
    with pytest.raises(RuntimeRootBindingError, match="权限"):
        binding.verify()

    root.chmod(original_mode)
    with pytest.raises(RuntimeRootBindingError, match="永久失效"):
        binding.verify()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test检测到漂移后活动租约即使旧inode恢复也永久拒绝(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    binding = _binding(root)

    with pytest.raises(RuntimeRootBindingError, match="永久失效"):
        with binding.bind() as bound_root:
            original = _replace_visible_root(root, replacement)
            with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
                bound_root.verify_visible()

            root.rename(replacement)
            original.rename(root)
            with pytest.raises(RuntimeRootBindingError, match="永久失效"):
                bound_root.verify_visible()


@pytest.mark.skipif(not _POSIX, reason="descriptor-bound 根租约仅在 WSL/Linux 验证")
def test相对路径拒绝根外路径非规范路径和根自身(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()

    with _binding(root).bind() as bound_root:
        for invalid in (
            tmp_path / "outside.json",
            root / ".." / "runtime" / "state.json",
            root,
        ):
            with pytest.raises(RuntimeRootBindingError, match="路径|叶子|逃逸"):
                bound_root.relative_path(invalid)
