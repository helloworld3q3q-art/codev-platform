from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from codev_platform.runtime_object_access import (
    RuntimeObjectAccessError,
    canonical_runtime_object_mode,
)
from codev_platform import runtime_object_access


@pytest.mark.parametrize("mode", [0o700, 0o711, 0o755])
def test_directory_modes_map_to_canonical_group_read_execute(mode: int) -> None:
    assert canonical_runtime_object_mode(stat.S_IFDIR | mode) == 0o750


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (0o600, 0o640),
        (0o644, 0o640),
        (0o610, 0o640),
        (0o601, 0o640),
        (0o711, 0o750),
        (0o755, 0o750),
    ],
)
def test_regular_file_modes_preserve_only_owner_executable_intent(
    mode: int,
    expected: int,
) -> None:
    assert canonical_runtime_object_mode(stat.S_IFREG | mode) == expected


def test_public_mode_policy_rejects_special_file() -> None:
    with pytest.raises(RuntimeObjectAccessError, match="特殊文件"):
        canonical_runtime_object_mode(stat.S_IFIFO | 0o600)


def test_public_entry_points_apply_linux_root_and_mutation_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        runtime_object_access,
        "walk_runtime_tree",
        lambda root, operation: calls.append(("tree", root, operation)),
        raising=False,
    )
    root = tmp_path / "object"

    runtime_object_access.seal_runtime_object_access(root)
    runtime_object_access.verify_runtime_object_access(root)

    assert len(calls) == 2
    assert calls[0][:2] == ("tree", root)
    assert isinstance(calls[0][2], runtime_object_access.CanonicalizeMode)
    assert calls[1][:2] == ("tree", root)
    assert isinstance(calls[1][2], runtime_object_access.VerifyOnly)


_LINUX_ROOT = sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0


def _linux_root_object(tmp_path: Path) -> Path:
    root = tmp_path / "object"
    root.mkdir(mode=0o700)
    marker = root / ".incomplete"
    marker.write_bytes(b"after_install\n")
    os.chmod(marker, 0o600)
    return root


def _tree_metadata(root: Path) -> dict[str, tuple[int, ...]]:
    paths = (root, *sorted(root.rglob("*")))
    return {
        path.relative_to(root.parent).as_posix(): (
            path.lstat().st_mode,
            path.lstat().st_uid,
            path.lstat().st_gid,
            path.lstat().st_nlink,
            path.lstat().st_size,
            path.lstat().st_mtime_ns,
            path.lstat().st_ctime_ns,
        )
        for path in paths
    }


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative mode 集成要求 Linux root")
def test_linux_seal_is_idempotent_no_follow_and_verify_is_metadata_read_only(
    tmp_path: Path,
) -> None:
    root = _linux_root_object(tmp_path)
    package = root / "venv" / "package"
    package.mkdir(parents=True, mode=0o700)
    module = package / "module.py"
    module.write_bytes(b"value = 1\n")
    os.chmod(module, 0o600)
    executable = root / "venv" / "bin-python"
    executable.write_bytes(b"python")
    os.chmod(executable, 0o711)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    os.chmod(outside, 0o600)
    (root / "outside-link").symlink_to(outside)

    runtime_object_access.seal_runtime_object_access(root)
    runtime_object_access.seal_runtime_object_access(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o750
    assert stat.S_IMODE(package.stat().st_mode) == 0o750
    assert stat.S_IMODE(module.stat().st_mode) == 0o640
    assert stat.S_IMODE(executable.stat().st_mode) == 0o750
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600
    before = _tree_metadata(root)
    runtime_object_access.verify_runtime_object_access(root)
    assert _tree_metadata(root) == before


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative mode 集成要求 Linux root")
def test_linux_seal_refuses_completed_object_before_chmod(tmp_path: Path) -> None:
    root = tmp_path / "completed"
    root.mkdir(mode=0o700)
    (root / "payload").write_bytes(b"payload")
    os.chmod(root, 0o700)

    with pytest.raises(RuntimeObjectAccessError, match="未完成标记"):
        runtime_object_access.seal_runtime_object_access(root)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative mode 集成要求 Linux root")
def test_linux_verify_rejects_mode_drift_without_repair(tmp_path: Path) -> None:
    root = _linux_root_object(tmp_path)
    payload = root / "payload"
    payload.write_bytes(b"payload")
    os.chmod(payload, 0o600)
    runtime_object_access.seal_runtime_object_access(root)
    os.chmod(payload, 0o660)

    with pytest.raises(RuntimeObjectAccessError, match="非 root 写"):
        runtime_object_access.verify_runtime_object_access(root)

    assert stat.S_IMODE(payload.stat().st_mode) == 0o660


@pytest.mark.skipif(not _LINUX_ROOT, reason="fd-relative mode 集成要求 Linux root")
def test_linux_seal_rejects_special_file_and_extended_attribute(tmp_path: Path) -> None:
    root = _linux_root_object(tmp_path)
    fifo = root / "unexpected"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeObjectAccessError, match="特殊文件"):
        runtime_object_access.seal_runtime_object_access(root)
    fifo.unlink()
    payload = root / "payload"
    payload.write_bytes(b"payload")
    try:
        os.setxattr(payload, "user.runtime-test", b"1")
    except OSError:
        pytest.skip("测试文件系统不支持扩展属性")
    with pytest.raises(RuntimeObjectAccessError, match="扩展属性或 ACL"):
        runtime_object_access.seal_runtime_object_access(root)
