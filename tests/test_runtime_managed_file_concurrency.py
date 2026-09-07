"""运行时受管文件的协作写锁与父目录并发测试。"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

import codev_platform._runtime_managed_file_bound as managed
from codev_platform.runtime_managed_file import (
    ManagedFilePolicy,
    create_managed_bytes_exclusive,
    write_managed_bytes_atomic,
)

_POSIX = os.name == "posix"


def _policy() -> ManagedFilePolicy:
    return ManagedFilePolicy(mode=0o600, require_uid=os.geteuid(), max_bytes=128)


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_atomic_replace_serializes_cooperative_writers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "state.json"
    target.write_bytes(b"original")
    target.chmod(0o600)
    original_replace = managed._replace_at
    original_lock = managed._lock_managed_parent
    first_at_replace = threading.Event()
    release_first = threading.Event()
    second_done = threading.Event()
    second_at_lock = threading.Event()
    count_lock = threading.Lock()
    replace_count = 0
    errors: list[BaseException] = []

    def block_first_replace(
        source: str,
        destination: str,
        source_parent: int,
        destination_parent: int,
    ) -> None:
        nonlocal replace_count
        with count_lock:
            replace_count += 1
            current = replace_count
        if current == 1:
            first_at_replace.set()
            assert release_first.wait(timeout=5)
        original_replace(source, destination, source_parent, destination_parent)

    def write(payload: bytes, *, done: threading.Event | None = None) -> None:
        try:
            write_managed_bytes_atomic(target, payload, root=root, policy=_policy())
        except BaseException as error:
            errors.append(error)
        finally:
            if done is not None:
                done.set()

    def record_lock_attempt(descriptor: int) -> None:
        if first_at_replace.is_set():
            second_at_lock.set()
        original_lock(descriptor)

    monkeypatch.setattr(managed, "_replace_at", block_first_replace)
    monkeypatch.setattr(managed, "_lock_managed_parent", record_lock_attempt)
    first = threading.Thread(target=write, args=(b"first",))
    first.start()
    assert first_at_replace.wait(timeout=5)
    second = threading.Thread(target=write, args=(b"second",), kwargs={"done": second_done})
    second.start()

    assert second_at_lock.wait(timeout=5)
    assert not second_done.is_set()
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert target.read_bytes() == b"second"


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_concurrent_parent_creation_reopens_and_verifies_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempts" / "attempt.json"
    original_mkdir = managed._mkdir_at
    original_fsync = managed._fsync
    injected = False
    synced_inodes: set[int] = set()

    def create_as_winner_then_report_conflict(
        name: str,
        mode: int,
        parent_descriptor: int,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            original_mkdir(name, mode, parent_descriptor)
            raise FileExistsError("并发写者已创建父目录")
        original_mkdir(name, mode, parent_descriptor)

    def record_fsync(descriptor: int) -> None:
        synced_inodes.add(os.fstat(descriptor).st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(managed, "_mkdir_at", create_as_winner_then_report_conflict)
    monkeypatch.setattr(managed, "_fsync", record_fsync)
    create_managed_bytes_exclusive(target, b"payload", root=root, policy=_policy())

    assert target.read_bytes() == b"payload"
    assert root.stat().st_ino in synced_inodes


@pytest.mark.skipif(not _POSIX, reason="descriptor-safe dirfd 原语在 WSL/Linux 验证")
def test_preopened_concurrent_parent_is_adopted_with_parent_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    target = root / "attempts" / "attempt.json"
    original_open = managed._open_at
    original_mkdir = managed._mkdir_at
    original_fsync = managed._fsync
    injected = False
    synced_inodes: set[int] = set()

    def create_before_first_open(
        name: str,
        flags: int,
        *,
        dir_fd: int | None = None,
        mode: int = 0o777,
    ) -> int:
        nonlocal injected
        if name == "attempts" and dir_fd is not None and not injected:
            injected = True
            original_mkdir(name, 0o755, dir_fd)
        return original_open(name, flags, dir_fd=dir_fd, mode=mode)

    def record_fsync(descriptor: int) -> None:
        synced_inodes.add(os.fstat(descriptor).st_ino)
        original_fsync(descriptor)

    monkeypatch.setattr(managed, "_open_at", create_before_first_open)
    monkeypatch.setattr(managed, "_fsync", record_fsync)
    create_managed_bytes_exclusive(target, b"payload", root=root, policy=_policy())

    assert target.read_bytes() == b"payload"
    assert root.stat().st_ino in synced_inodes
