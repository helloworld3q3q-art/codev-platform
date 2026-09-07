"""版本化运行时受管存储、锁与半成品隔离测试。"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import codev_platform.runtime_storage as storage
from codev_platform.runtime_storage import (
    RuntimeStorageLockError,
    RuntimeStoragePathError,
    activation_lock,
    id_lock,
)

_BASE_ID = "1" * 64
_OTHER_BASE_ID = "2" * 64
_RELEASE_ID = "3" * 64
_POSIX = os.name == "posix"


def _symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"当前测试环境不能创建符号链接：{error}")


def _lock_probe(
    root: Path,
    kind: str,
    object_id: str,
    *,
    shared: bool,
    sentinel: Path,
) -> subprocess.Popen[str]:
    script = """
import sys
from pathlib import Path
from codev_platform.runtime_storage import id_lock

with id_lock(Path(sys.argv[1]), sys.argv[2], sys.argv[3], shared=sys.argv[4] == "1"):
    Path(sys.argv[5]).write_text("acquired", encoding="utf-8")
"""
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(root),
            kind,
            object_id,
            "1" if shared else "0",
            str(sentinel),
        ],
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _wait_for_sentinel(process: subprocess.Popen[str], sentinel: Path) -> None:
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert sentinel.read_text(encoding="utf-8") == "acquired"


def test_id_lock_rejects_relative_root_and_noncanonical_identifiers(tmp_path: Path) -> None:
    with pytest.raises(RuntimeStoragePathError, match="绝对路径"):
        with id_lock(Path("relative-root"), "base", _BASE_ID, shared=False):
            pass

    invalid_values = ("../escape", "a" * 63, "A" * 64, "0" * 64)
    for invalid in invalid_values:
        with pytest.raises(RuntimeStoragePathError, match="64 位"):
            with id_lock(tmp_path, "base", invalid, shared=False):
                pass

    with pytest.raises(RuntimeStoragePathError, match="kind"):
        with id_lock(tmp_path, "artifact", _BASE_ID, shared=False):
            pass
    with pytest.raises(TypeError, match="shared"):
        with id_lock(tmp_path, "base", _BASE_ID, shared=1):  # type: ignore[arg-type]
            pass


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_id_lock_rejects_symlinked_root_or_lock_path(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    _symlink(linked_root, outside, directory=True)
    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        with id_lock(linked_root, "base", _BASE_ID, shared=False):
            pass

    root = tmp_path / "root"
    root.mkdir()
    _symlink(root / "locks", outside, directory=True)
    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        with id_lock(root, "base", _BASE_ID, shared=False):
            pass
    assert not (outside / "base").exists()


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_id_lock_keeps_stable_kind_scoped_lock_files(tmp_path: Path) -> None:
    with id_lock(tmp_path, "base", _BASE_ID, shared=False):
        lock_path = tmp_path / "locks" / "base" / f"{_BASE_ID}.lock"
        assert lock_path.is_file()
    assert lock_path.is_file()

    with id_lock(tmp_path, "release", _RELEASE_ID, shared=True):
        release_lock = tmp_path / "locks" / "release" / f"{_RELEASE_ID}.lock"
        assert release_lock.is_file()
    assert release_lock.is_file()


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_lock_file_is_private_and_shared_lock_reopens_same_secure_inode(
    tmp_path: Path,
) -> None:
    with id_lock(tmp_path, "base", _BASE_ID, shared=False):
        lock_path = tmp_path / "locks" / "base" / f"{_BASE_ID}.lock"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600

    with id_lock(tmp_path, "base", _BASE_ID, shared=True):
        assert lock_path.is_file()


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644, 0o660))
def test_lock_rejects_unsafe_or_noncanonical_existing_mode(tmp_path: Path, mode: int) -> None:
    with id_lock(tmp_path, "base", _BASE_ID, shared=False):
        lock_path = tmp_path / "locks" / "base" / f"{_BASE_ID}.lock"
    lock_path.chmod(mode)

    with pytest.raises(RuntimeStorageLockError, match="权限"):
        with id_lock(tmp_path, "base", _BASE_ID, shared=True):
            pass


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_lock_hardlink_identity_violation_remains_lock_error(tmp_path: Path) -> None:
    with id_lock(tmp_path, "base", _BASE_ID, shared=False):
        lock_path = tmp_path / "locks" / "base" / f"{_BASE_ID}.lock"
    os.link(lock_path, tmp_path / "unexpected-hardlink")

    with pytest.raises(RuntimeStorageLockError, match="所有权|权限|inode"):
        with id_lock(tmp_path, "base", _BASE_ID, shared=True):
            pass


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_lock_replacement_after_flock_is_detected_before_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = storage._flock

    def replace_after_lock(descriptor: int, *, shared: bool) -> None:
        original(descriptor, shared=shared)
        lock_path = tmp_path / "locks" / "base" / f"{_BASE_ID}.lock"
        lock_path.unlink()
        lock_path.write_bytes(b"")
        lock_path.chmod(0o600)

    monkeypatch.setattr(storage, "_flock", replace_after_lock)

    with pytest.raises(RuntimeStorageLockError, match="inode"):
        with id_lock(tmp_path, "base", _BASE_ID, shared=False):
            pytest.fail("分裂锁不得进入事务主体")


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_id_lock_preserves_body_oserror_without_misreporting_lock_failure(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="业务操作失败") as caught:
        with id_lock(tmp_path, "base", _BASE_ID, shared=False):
            raise OSError("业务操作失败")

    assert type(caught.value) is OSError


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_same_id_exclusive_lock_serializes_and_shared_lock_can_overlap(tmp_path: Path) -> None:
    exclusive_sentinel = tmp_path / "exclusive-acquired"
    with id_lock(tmp_path, "base", _BASE_ID, shared=False):
        process = _lock_probe(
            tmp_path,
            "base",
            _BASE_ID,
            shared=False,
            sentinel=exclusive_sentinel,
        )
        time.sleep(0.3)
        assert process.poll() is None
        assert not exclusive_sentinel.exists()
    _wait_for_sentinel(process, exclusive_sentinel)

    shared_sentinel = tmp_path / "shared-acquired"
    with id_lock(tmp_path, "base", _BASE_ID, shared=True):
        process = _lock_probe(
            tmp_path,
            "base",
            _BASE_ID,
            shared=True,
            sentinel=shared_sentinel,
        )
        _wait_for_sentinel(process, shared_sentinel)


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_shared_lock_blocks_writer_but_different_ids_do_not_share_lock(tmp_path: Path) -> None:
    same_id_sentinel = tmp_path / "same-id-writer"
    with id_lock(tmp_path, "base", _BASE_ID, shared=True):
        blocked = _lock_probe(
            tmp_path,
            "base",
            _BASE_ID,
            shared=False,
            sentinel=same_id_sentinel,
        )
        time.sleep(0.3)
        assert blocked.poll() is None
        assert not same_id_sentinel.exists()

        other_id_sentinel = tmp_path / "other-id-writer"
        independent = _lock_probe(
            tmp_path,
            "base",
            _OTHER_BASE_ID,
            shared=False,
            sentinel=other_id_sentinel,
        )
        _wait_for_sentinel(independent, other_id_sentinel)
    _wait_for_sentinel(blocked, same_id_sentinel)


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_activation_lock_is_stable_and_exclusive(tmp_path: Path) -> None:
    sentinel = tmp_path / "activation-acquired"
    script = """
import sys
from pathlib import Path
from codev_platform.runtime_storage import activation_lock

with activation_lock(Path(sys.argv[1])):
    Path(sys.argv[2]).write_text("acquired", encoding="utf-8")
"""
    with activation_lock(tmp_path):
        lock_path = tmp_path / "locks" / "activation.lock"
        assert lock_path.is_file()
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), str(sentinel)],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.3)
        assert process.poll() is None
        assert not sentinel.exists()
    _wait_for_sentinel(process, sentinel)
    assert lock_path.is_file()


def test_activation_lock_rejects_non_boolean_shared_mode(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="shared"):
        with activation_lock(tmp_path, shared=1):  # type: ignore[arg-type]
            pass


@pytest.mark.skipif(not _POSIX, reason="POSIX flock 仅在 WSL/Linux 验证")
def test_shared_activation_lock_allows_readers_and_blocks_writer(tmp_path: Path) -> None:
    shared_sentinel = tmp_path / "activation-shared-acquired"
    exclusive_sentinel = tmp_path / "activation-exclusive-acquired"
    script = """
import sys
from pathlib import Path
from codev_platform.runtime_storage import activation_lock

with activation_lock(Path(sys.argv[1]), shared=sys.argv[2] == "1"):
    Path(sys.argv[3]).write_text("acquired", encoding="utf-8")
"""
    with activation_lock(tmp_path, shared=True):
        shared_process = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), "1", str(shared_sentinel)],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _wait_for_sentinel(shared_process, shared_sentinel)

        exclusive_process = subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), "0", str(exclusive_sentinel)],
            cwd=Path.cwd(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.3)
        assert exclusive_process.poll() is None
        assert not exclusive_sentinel.exists()

    _wait_for_sentinel(exclusive_process, exclusive_sentinel)


def test_non_posix_locking_fails_closed(tmp_path: Path) -> None:
    if _POSIX:
        pytest.skip("仅验证非 POSIX 失败关闭")
    with pytest.raises(RuntimeStorageLockError, match="POSIX"):
        with id_lock(tmp_path, "base", _BASE_ID, shared=False):
            pass
    with pytest.raises(RuntimeStorageLockError, match="POSIX"):
        with activation_lock(tmp_path):
            pass
