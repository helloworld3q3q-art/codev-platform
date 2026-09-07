"""reindex 协议文件的安全临时写入与耐久发布测试。"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from codev_platform.reindex import file_durability


def test_有界读取普通文件并拒绝超限和目录(tmp_path: Path) -> None:
    target = tmp_path / "spec.json"
    target.write_bytes(b"complete")

    assert file_durability.read_regular_file_bounded(
        target,
        max_bytes=8,
    ) == b"complete"
    with pytest.raises(ValueError, match="大小上限"):
        file_durability.read_regular_file_bounded(target, max_bytes=7)
    with pytest.raises(ValueError, match="普通文件"):
        file_durability.read_regular_file_bounded(tmp_path, max_bytes=8)


def test_有界读取拒绝符号链接或_reparse_point(tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    link = tmp_path / "linked-result.json"
    target.write_bytes(b"result")
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建链接：{exc}")

    with pytest.raises((OSError, ValueError)):
        file_durability.read_regular_file_bounded(link, max_bytes=16)


def test_有界读取拒绝字符设备() -> None:
    with pytest.raises(ValueError, match="普通文件"):
        file_durability.read_regular_file_bounded(Path(os.devnull), max_bytes=16)


@pytest.mark.skipif(os.name == "nt", reason="POSIX 安全打开标志测试")
def test_posix_有界读取显式使用_nofollow_cloexec_nonblock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "spec.json"
    target.write_bytes(b"spec")
    real_open = os.open
    seen: list[int] = []

    def _open(path: str | bytes | Path, flags: int, mode: int = 0o777) -> int:
        seen.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(file_durability.os, "open", _open)

    assert file_durability.read_regular_file_bounded(target, max_bytes=16) == b"spec"
    required = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    assert seen == [os.O_RDONLY | required]


@pytest.mark.skipif(os.name != "nt", reason="Windows 设备命名空间测试")
def test_windows_有界读取在打开前拒绝设备命名空间() -> None:
    with pytest.raises(ValueError, match="设备命名空间"):
        file_durability.read_regular_file_bounded(
            Path(r"\\.\pipe\codev-never-open"),
            max_bytes=16,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows 设备命名空间测试")
def test_windows_耐久写入在创建父目录前拒绝设备命名空间() -> None:
    with pytest.raises(ValueError, match="设备命名空间"):
        file_durability.durable_write_once(
            Path(r"\\.\pipe\codev-never-create\receipt.json"),
            b"secret",
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO 行为测试")
def test_有界读取_fifo_不等待写端并立即拒绝(tmp_path: Path) -> None:
    target = tmp_path / "blocked.fifo"
    os.mkfifo(target)

    started = time.monotonic()
    with pytest.raises(ValueError, match="普通文件"):
        file_durability.read_regular_file_bounded(target, max_bytes=16)

    assert time.monotonic() - started < 1.0


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL 故障注入")
def test_windows_owner_only_acl_失败时不发布目标(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"

    def _fail_acl() -> str:
        raise PermissionError("注入 ACL 收紧失败")

    monkeypatch.setattr(file_durability, "_windows_current_user_sid", _fail_acl)

    with pytest.raises(PermissionError, match="ACL 收紧失败"):
        file_durability.durable_write_once(target, b"secret")

    assert target.exists() is False
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows 原子 ACL 创建路径")
def test_windows_owner_only_acl_必须在创建时原子附加(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "journal.json"

    def _unexpected_open(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("Windows 协议临时文件不能先继承 ACL 再事后收紧")

    monkeypatch.setattr(file_durability.os, "open", _unexpected_open)

    file_durability.durable_write_once(target, b"claim-secret")

    assert target.read_bytes() == b"claim-secret"


def test_write_once_使用完整写循环且先同步文件再发布(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "receipt.json"
    payload = b"0123456789" * 20
    real_write = os.write
    real_fsync = os.fsync
    writes: list[int] = []
    visible_at_sync: list[bool] = []

    def _partial_write(fd: int, data: bytes) -> int:
        chunk = data[: max(1, len(data) // 3)]
        writes.append(len(chunk))
        return real_write(fd, chunk)

    def _record_fsync(fd: int) -> None:
        visible_at_sync.append(target.exists())
        real_fsync(fd)

    monkeypatch.setattr(file_durability.os, "write", _partial_write)
    monkeypatch.setattr(file_durability.os, "fsync", _record_fsync)

    file_durability.durable_write_once(target, payload)

    assert target.read_bytes() == payload
    assert len(writes) > 1
    assert visible_at_sync[0] is False


@pytest.mark.skipif(os.name == "nt", reason="Windows 使用带 protected DACL 的 CreateFileW")
def test_临时文件使用排他创建且禁止跟随符号链接(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "spec.json"
    real_open = os.open
    seen_flags: list[int] = []

    def _record_open(path: str | bytes | Path, flags: int, mode: int = 0o777) -> int:
        if Path(path).name.endswith(".tmp"):
            seen_flags.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(file_durability.os, "open", _record_open)

    file_durability.durable_write_once(target, b"payload")

    assert len(seen_flags) == 1
    required = os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    assert seen_flags[0] & required == required


def test_写入中途失败不暴露目标并清理自有临时文件(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "result.json"
    real_write = os.write
    calls = 0

    def _fail_after_partial(fd: int, data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, data[:1])
        raise OSError("注入写入失败")

    monkeypatch.setattr(file_durability.os, "write", _fail_after_partial)

    with pytest.raises(OSError, match="注入写入失败"):
        file_durability.durable_write_once(target, b"complete-payload")

    assert target.exists() is False
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_临时文件同步失败不发布目标并清理自有临时文件(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "completion.json"

    def _fail_sync(_descriptor: int) -> None:
        raise OSError("注入文件同步失败")

    monkeypatch.setattr(file_durability.os, "fsync", _fail_sync)

    with pytest.raises(OSError, match="文件同步失败"):
        file_durability.durable_write_once(target, b"complete-payload")

    assert target.exists() is False
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_原子替换失败保留旧目标且不暴露新半文件(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "journal.json"
    target.write_bytes(b"old")

    def _fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("注入替换失败")

    monkeypatch.setattr(file_durability, "_publish_replace", _fail_replace)

    with pytest.raises(OSError, match="注入替换失败"):
        file_durability.durable_write_replace(target, b"new-complete")

    assert target.read_bytes() == b"old"
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []


def test_write_once_拒绝覆盖并保留既有目标(tmp_path: Path) -> None:
    target = tmp_path / "result.json"
    target.write_bytes(b"first")

    with pytest.raises(FileExistsError):
        file_durability.durable_write_once(target, b"second")

    assert target.read_bytes() == b"first"


def test_耐久替换发布完整新内容(tmp_path: Path) -> None:
    target = tmp_path / "journal.json"
    target.write_bytes(b"old")

    file_durability.durable_write_replace(target, b"new-complete")

    assert target.read_bytes() == b"new-complete"


@pytest.mark.skipif(os.name == "nt", reason="POSIX 权限位测试")
def test_umask_022_下协议文件仍仅当前用户可读写(tmp_path: Path) -> None:
    target = tmp_path / "journal.json"
    previous = os.umask(0o022)
    try:
        file_durability.durable_write_replace(target, b"claim-secret")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
