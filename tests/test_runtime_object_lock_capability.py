from __future__ import annotations

import os
from pathlib import Path

import pytest

from codev_platform.runtime_object_lock_capability import (
    BoundRuntimeObjectLock,
    RuntimeObjectLockCapabilityError,
)
from codev_platform.runtime_storage import (
    RuntimeStoragePathError,
    isolate_incomplete_locked_at,
    object_lock,
)


_BASE_ID = "1" * 64
_RELEASE_ID = "3" * 64
_POSIX = os.name == "posix"


def _incomplete_base(root: Path) -> Path:
    source = root / "bases" / _BASE_ID
    source.mkdir(parents=True)
    (source / ".incomplete").write_text("after_install", encoding="utf-8")
    return source


@pytest.mark.skipif(not _POSIX, reason="对象锁 capability 仅在 WSL/Linux 验证")
def test伪造对象锁不能伪装为活动排他锁() -> None:
    forged = object.__new__(BoundRuntimeObjectLock)

    with pytest.raises(RuntimeObjectLockCapabilityError, match="未经签发|已失效"):
        with forged.hold_active(kind="base", object_id=_BASE_ID, exclusive=True):
            pytest.fail("伪造 capability 不得进入锁域")


@pytest.mark.skipif(not _POSIX, reason="对象锁 capability 仅在 WSL/Linux 验证")
def test共享对象锁不能触发隔离(tmp_path: Path) -> None:
    source = _incomplete_base(tmp_path)

    with object_lock(tmp_path, "base", _BASE_ID, shared=True) as lock:
        with pytest.raises(RuntimeStoragePathError, match="排他"):
            isolate_incomplete_locked_at(lock, "base", _BASE_ID)

    assert source.is_dir()
    assert not (tmp_path / "journal").exists()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(not _POSIX, reason="对象锁 capability 仅在 WSL/Linux 验证")
def test错对象的排他锁不能隔离其他对象(tmp_path: Path) -> None:
    source = _incomplete_base(tmp_path)

    with object_lock(tmp_path, "release", _RELEASE_ID, shared=False) as lock:
        with pytest.raises(RuntimeStoragePathError, match="身份"):
            isolate_incomplete_locked_at(lock, "base", _BASE_ID)

    assert source.is_dir()
    assert not (tmp_path / "journal").exists()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(not _POSIX, reason="对象锁 capability 仅在 WSL/Linux 验证")
def test对象锁退出后隔离立即闭锁(tmp_path: Path) -> None:
    source = _incomplete_base(tmp_path)
    with object_lock(tmp_path, "base", _BASE_ID, shared=False) as lock:
        leaked = lock

    with pytest.raises(RuntimeStoragePathError, match="对象锁"):
        isolate_incomplete_locked_at(leaked, "base", _BASE_ID)

    assert source.is_dir()
    assert not (tmp_path / "journal").exists()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(
    not _POSIX or not hasattr(os, "fork"),
    reason="fork 与对象锁 capability 仅在 WSL/Linux 验证",
)
def testfork子进程不能重放父进程活动对象锁隔离(
    tmp_path: Path,
) -> None:
    source = _incomplete_base(tmp_path)
    start_read, start_write = os.pipe()
    result_read, result_write = os.pipe()
    child_pid: int | None = None
    try:
        with object_lock(tmp_path, "base", _BASE_ID, shared=False) as lock:
            child_pid = os.fork()
            if child_pid == 0:
                os.close(start_write)
                os.close(result_read)
                try:
                    assert os.read(start_read, 2) == b"go"
                    try:
                        isolate_incomplete_locked_at(lock, "base", _BASE_ID)
                    except RuntimeStoragePathError:
                        os.write(result_write, b"blocked")
                    else:
                        os.write(result_write, b"accepted")
                except BaseException:
                    os.write(result_write, b"error")
                finally:
                    os.close(start_read)
                    os.close(result_write)
                os._exit(0)
        os.close(start_read)
        os.close(result_write)
        assert child_pid is not None
        assert os.write(start_write, b"go") == 2
        assert os.read(result_read, 16) == b"blocked"
        _pid, status = os.waitpid(child_pid, 0)
        child_pid = None
        assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for descriptor in (start_read, start_write, result_read, result_write):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if child_pid is not None:
            _pid, _status = os.waitpid(child_pid, 0)

    assert source.is_dir()
    assert not (tmp_path / "journal").exists()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(not _POSIX, reason="对象锁 capability 仅在 WSL/Linux 验证")
def test活动排他对象锁可以隔离同一对象(tmp_path: Path) -> None:
    source = _incomplete_base(tmp_path)

    with object_lock(tmp_path, "base", _BASE_ID, shared=False) as lock:
        quarantined = isolate_incomplete_locked_at(lock, "base", _BASE_ID)

    assert quarantined is not None
    assert not source.exists()
    assert (quarantined / ".incomplete").is_file()
