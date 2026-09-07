"""基于同一根 descriptor 的受管文件与锁原语测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import codev_platform._runtime_managed_file_bound as bound_file
import codev_platform.runtime_storage as storage
from codev_platform.runtime_managed_file import ManagedFileError, ManagedFilePolicy
from codev_platform.runtime_root_binding import RuntimeRootBinding, RuntimeRootBindingError
from codev_platform.runtime_storage import (
    RuntimeStorageLockError,
    activation_lock_at,
    deployment_lock_at,
    id_lock_at,
)


_POSIX = os.name == "posix"


def _policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(mode=0o600, require_uid=os.geteuid(), max_bytes=256)


def _binding(root: Path) -> RuntimeRootBinding:
    return RuntimeRootBinding(root, owner_uid=os.geteuid())


def _replace_visible_root(root: Path, replacement: Path) -> Path:
    displaced = root.with_name(root.name + "-displaced")
    root.rename(displaced)
    replacement.rename(root)
    return displaced


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
def test受管at原语在同一根租约内读写可选读与精确删除(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "state.json"

    with _binding(root).bind() as bound_root:
        assert (
            bound_file.read_optional_managed_bytes_at(
                target,
                root=bound_root,
                policy=_policy(),
            )
            is None
        )
        created = bound_file.create_managed_bytes_exclusive_at(
            target,
            b"first",
            root=bound_root,
            policy=_policy(),
        )
        replaced = bound_file.write_managed_bytes_atomic_at(
            target,
            b"second",
            root=bound_root,
            policy=_policy(),
        )

        assert created.path == "state.json"
        assert replaced.path == "state.json"
        assert (
            bound_file.read_managed_bytes_at(
                target,
                root=bound_root,
                policy=_policy(),
            )
            == b"second"
        )
        with pytest.raises(ManagedFileError, match="不一致"):
            bound_file.remove_managed_bytes_exact_at(
                target,
                b"first",
                root=bound_root,
                policy=_policy(),
            )
        assert bound_file.remove_managed_bytes_exact_at(
            target,
            b"second",
            root=bound_root,
            policy=_policy(),
        )
        assert not bound_file.remove_managed_bytes_exact_at(
            target,
            b"second",
            root=bound_root,
            policy=_policy(),
        )


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
def test绑定发布证据包含叶子gid(tmp_path: Path) -> None:
    """受管发布的非秘密证据必须包含最终 descriptor 的 gid。"""
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "state.json"

    with _binding(root).bind() as bound_root:
        evidence = bound_file.create_managed_bytes_exclusive_at(
            target,
            b"state",
            root=bound_root,
            policy=_policy(),
        )

    assert evidence.gid == os.getegid()


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
def test可选读只把安全最终叶子缺失视为不存在(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()

    with _binding(root).bind() as bound_root:
        assert (
            bound_file.read_optional_managed_bytes_at(
                root / "missing" / "state.json",
                root=bound_root,
                policy=_policy(),
                allow_missing_parent=True,
            )
            is None
        )
        with pytest.raises(ManagedFileError, match="父目录不存在"):
            bound_file.read_optional_managed_bytes_at(
                root / "missing" / "state.json",
                root=bound_root,
                policy=_policy(),
            )

        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "linked").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ManagedFileError, match="符号链接|安全打开"):
            bound_file.read_optional_managed_bytes_at(
                root / "linked" / "state.json",
                root=bound_root,
                policy=_policy(),
            )
        with pytest.raises(RuntimeRootBindingError, match="逃逸"):
            bound_file.read_managed_bytes_at(
                tmp_path / "outside.json",
                root=bound_root,
                policy=_policy(),
            )


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
def test已关闭租约拒绝后续at文件操作并永久闭锁绑定(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    binding = _binding(root)

    with binding.bind() as bound_root:
        pass

    with pytest.raises(RuntimeRootBindingError, match="已关闭"):
        bound_file.read_optional_managed_bytes_at(
            root / "state.json",
            root=bound_root,
            policy=_policy(),
        )
    with pytest.raises(RuntimeRootBindingError, match="永久失效"):
        binding.verify()


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
@pytest.mark.parametrize("operation", ("replace", "exclusive"))
def test最后写前根替换只影响已脱离根且替换根无新叶子(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    target = root / "attempts" / "state.json"

    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        with _binding(root).bind() as bound_root:
            if operation == "replace":
                original_publish = bound_file._replace_at

                def replace_after_final_check(
                    source: str,
                    destination: str,
                    source_parent: int,
                    destination_parent: int,
                ) -> None:
                    _replace_visible_root(root, replacement)
                    original_publish(source, destination, source_parent, destination_parent)

                monkeypatch.setattr(bound_file, "_replace_at", replace_after_final_check)
                writer = bound_file.write_managed_bytes_atomic_at
            else:
                original_publish = bound_file._rename_noreplace_at

                def create_after_final_check(
                    source: str,
                    destination: str,
                    source_parent: int,
                    destination_parent: int,
                ) -> None:
                    _replace_visible_root(root, replacement)
                    original_publish(source, destination, source_parent, destination_parent)

                monkeypatch.setattr(bound_file, "_rename_noreplace_at", create_after_final_check)
                writer = bound_file.create_managed_bytes_exclusive_at

            with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
                writer(target, b"published-on-held-fd", root=bound_root, policy=_policy())

            assert not (root / "attempts" / "state.json").exists()
            with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
                bound_root.verify_visible()

    assert not (root / "attempts" / "state.json").exists()


@pytest.mark.skipif(not _POSIX, reason="bound 文件原语仅在 WSL/Linux 验证")
def test精确删除最后检查后替换根不会删除替换根叶子(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    target = root / "state.json"
    target.write_bytes(b"old-visible-root")
    (replacement / "state.json").write_bytes(b"new-visible-root")
    target.chmod(0o600)
    (replacement / "state.json").chmod(0o600)
    original_unlink = bound_file._unlink_at

    def delete_after_final_check(name: str, parent_descriptor: int) -> None:
        _replace_visible_root(root, replacement)
        original_unlink(name, parent_descriptor)

    monkeypatch.setattr(bound_file, "_unlink_at", delete_after_final_check)
    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        with _binding(root).bind() as bound_root:
            with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
                bound_file.remove_managed_bytes_exact_at(
                    target,
                    b"old-visible-root",
                    root=bound_root,
                    policy=_policy(),
                )
            assert (root / "state.json").read_bytes() == b"new-visible-root"

    assert (root / "state.json").read_bytes() == b"new-visible-root"


@pytest.mark.skipif(not _POSIX, reason="bound 锁仅在 WSL/Linux 验证")
def test部署锁在同一根租约上取得后复验可见根(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "runtime"
    replacement = tmp_path / "replacement"
    root.mkdir()
    replacement.mkdir()
    original_flock = storage._flock

    def swap_after_lock(descriptor: int, *, shared: bool) -> None:
        original_flock(descriptor, shared=shared)
        _replace_visible_root(root, replacement)

    monkeypatch.setattr(storage, "_flock", swap_after_lock)
    with pytest.raises(RuntimeRootBindingError, match="身份|可见"):
        with _binding(root).bind() as bound_root:
            with pytest.raises(RuntimeStorageLockError, match="根目录|可见|身份"):
                with deployment_lock_at(bound_root):
                    pytest.fail("根替换后不得进入部署锁主体")

    assert not (root / "locks" / "deployment.lock").exists()


@pytest.mark.skipif(not _POSIX, reason="bound 锁仅在 WSL/Linux 验证")
def test三类绑定锁均在同一租约派生的受管目录内创建(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()

    with _binding(root).bind() as bound_root:
        with deployment_lock_at(bound_root):
            assert (root / "locks" / "deployment.lock").is_file()
        with activation_lock_at(bound_root, shared=True):
            assert (root / "locks" / "activation.lock").is_file()
        with id_lock_at(bound_root, "base", "1" * 64, shared=False):
            assert (root / "locks" / "base" / f"{'1' * 64}.lock").is_file()
