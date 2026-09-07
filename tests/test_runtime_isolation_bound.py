"""半成品隔离在同一 root binding 下的回归测试。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import codev_platform.runtime_isolation as isolation
from codev_platform.runtime_storage import (
    RuntimeStorageLockError,
    RuntimeStoragePathError,
    isolate_corrupt_completed_locked_at,
    isolate_incomplete_locked_at,
    object_lock,
)


_BASE_ID = "1" * 64
_RELEASE_ID = "3" * 64
_POSIX = os.name == "posix"


def _symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"当前测试环境不能创建符号链接：{error}")


def _incomplete_object(
    root: Path,
    kind: str,
    object_id: str,
    *,
    stage: str,
) -> Path:
    collection = "bases" if kind == "base" else "releases"
    object_dir = root / collection / object_id
    object_dir.mkdir(parents=True)
    (object_dir / ".incomplete").write_text(stage, encoding="utf-8")
    (object_dir / "payload.txt").write_text("保留半成品", encoding="utf-8")
    return object_dir


def _isolate_incomplete(root: Path, kind: str, object_id: str) -> Path | None:
    with object_lock(root, kind, object_id, shared=False) as lock:
        return isolate_incomplete_locked_at(lock, kind, object_id)


def _isolate_corrupt_completed(
    root: Path,
    kind: str,
    object_id: str,
) -> Path | None:
    with object_lock(root, kind, object_id, shared=False) as lock:
        return isolate_corrupt_completed_locked_at(lock, kind, object_id)


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


@pytest.mark.skipif(not _POSIX, reason="受管隔离仅在 WSL/Linux 验证")
def test隔离在intent发布前根替换时不触碰替换目录(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    source = root / "bases" / _BASE_ID
    source.mkdir(parents=True)
    (source / ".incomplete").write_text("after_install", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside")
    previous = tmp_path / "runtime-previous"
    original = isolation._write_intent

    def replace_then_write(
        path: Path,
        payload: bytes,
        bound_root: object,
    ) -> None:
        root.rename(previous)
        root.symlink_to(outside, target_is_directory=True)
        original(path, payload, bound_root)

    monkeypatch.setattr(isolation, "_write_intent", replace_then_write)

    with pytest.raises(RuntimeStoragePathError, match="运行时根"):
        with object_lock(root, "base", _BASE_ID, shared=False) as lock:
            isolate_incomplete_locked_at(lock, "base", _BASE_ID)

    assert sentinel.read_bytes() == b"outside"
    assert (previous / "bases" / _BASE_ID).is_dir()
    assert not (previous / "journal" / "pending").exists()
    assert not (outside / "journal").exists()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test未完成基座隔离后持久同步两个父目录并写入白名单审计(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    source_parent_identity = _directory_identity(source.parent)
    synced: list[tuple[int, int]] = []
    real_fsync = isolation.os.fsync

    def record_fsync(descriptor: int) -> None:
        metadata = os.fstat(descriptor)
        synced.append((metadata.st_dev, metadata.st_ino))
        real_fsync(descriptor)

    monkeypatch.setattr(isolation.os, "fsync", record_fsync)
    quarantined = _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert quarantined is not None
    assert not source.exists()
    assert quarantined.parent == tmp_path / "quarantine" / "incomplete" / "base"
    assert (quarantined / "payload.txt").read_text(encoding="utf-8") == "保留半成品"
    assert source_parent_identity in synced
    assert _directory_identity(quarantined.parent) in synced

    journal = tmp_path / "journal" / "runtime-storage.jsonl"
    raw = journal.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert set(record) == {
        "failure_stage",
        "id",
        "isolated_at",
        "isolation_relative_path",
        "kind",
    }
    assert record["kind"] == "base"
    assert record["id"] == _BASE_ID
    assert record["failure_stage"] == "after_install"
    assert record["isolation_relative_path"] == quarantined.relative_to(tmp_path).as_posix()
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z",
        record["isolated_at"],
    )


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test审计绝不复制未知marker内容或绝对路径(tmp_path: Path) -> None:
    secret = "marker-marker"
    _incomplete_object(tmp_path, "release", _RELEASE_ID, stage=secret)

    quarantined = _isolate_incomplete(tmp_path, "release", _RELEASE_ID)

    assert quarantined is not None
    raw = (tmp_path / "journal" / "runtime-storage.jsonl").read_text(encoding="utf-8")
    record = json.loads(raw)
    assert record["failure_stage"] == "unknown"
    assert secret not in raw
    assert str(tmp_path) not in raw


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
@pytest.mark.parametrize("mode", (0o600, 0o640, 0o644))
def test安全旧审计文件权限不阻断隔离恢复(tmp_path: Path, mode: int) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    journal = tmp_path / "journal" / "runtime-storage.jsonl"
    journal.parent.mkdir()
    journal.write_bytes(b"")
    journal.chmod(mode)

    quarantined = _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert quarantined is not None
    assert not source.exists()
    assert journal.stat().st_mode & 0o777 == mode


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
@pytest.mark.parametrize("mode", (0o660, 0o666, 0o777))
def test非属主可写旧审计文件仍必须拒绝恢复(tmp_path: Path, mode: int) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    journal = tmp_path / "journal" / "runtime-storage.jsonl"
    journal.parent.mkdir()
    journal.write_bytes(b"")
    journal.chmod(mode)

    with pytest.raises((RuntimeStorageLockError, RuntimeStoragePathError), match="权限"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert source.is_dir()
    assert journal.stat().st_mode & 0o777 == mode


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test_release最终元数据阶段保留在白名单审计中(tmp_path: Path) -> None:
    _incomplete_object(tmp_path, "release", _RELEASE_ID, stage="after_release_json")

    _isolate_incomplete(tmp_path, "release", _RELEASE_ID)

    raw = (tmp_path / "journal" / "runtime-storage.jsonl").read_text(encoding="utf-8")
    assert json.loads(raw)["failure_stage"] == "after_release_json"


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test隔离缺失或完整对象时不创建审计文件(tmp_path: Path) -> None:
    assert _isolate_incomplete(tmp_path, "base", _BASE_ID) is None
    complete = tmp_path / "bases" / _BASE_ID
    complete.mkdir(parents=True)
    (complete / "base.json").write_text("{}", encoding="utf-8")

    assert _isolate_incomplete(tmp_path, "base", _BASE_ID) is None
    assert complete.is_dir()
    assert not (tmp_path / "journal" / "runtime-storage.jsonl").exists()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test损坏完成对象隔离到独立审计分类(tmp_path: Path) -> None:
    source = tmp_path / "bases" / _BASE_ID
    source.mkdir(parents=True)
    (source / "base.json").write_text("损坏元数据", encoding="utf-8")
    (source / "payload.txt").write_text("保留损坏完成对象", encoding="utf-8")

    quarantined = _isolate_corrupt_completed(tmp_path, "base", _BASE_ID)

    assert quarantined is not None
    assert not source.exists()
    assert quarantined.parent == tmp_path / "quarantine" / "corrupt" / "base"
    assert (quarantined / "payload.txt").read_text(encoding="utf-8") == "保留损坏完成对象"
    record = json.loads(
        (tmp_path / "journal" / "runtime-storage.jsonl").read_text(encoding="utf-8")
    )
    assert record["failure_stage"] == "completed_corrupt"
    assert record["isolation_relative_path"] == quarantined.relative_to(tmp_path).as_posix()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test创建marker前崩溃的目录按缺marker隔离(tmp_path: Path) -> None:
    source = tmp_path / "bases" / _BASE_ID
    source.mkdir(parents=True)
    (source / "orphan.txt").write_text("mkdir 后崩溃", encoding="utf-8")

    quarantined = _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert quarantined is not None
    assert not source.exists()
    assert (quarantined / "orphan.txt").is_file()
    raw = (tmp_path / "journal" / "runtime-storage.jsonl").read_text(encoding="utf-8")
    assert json.loads(raw)["failure_stage"] == "marker_missing"


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test未完成对象符号链接逃逸时失败关闭(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".incomplete").write_text("after_venv", encoding="utf-8")
    collection = tmp_path / "bases"
    collection.mkdir()
    source = collection / _BASE_ID
    _symlink(source, outside, directory=True)

    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert source.is_symlink()
    assert (outside / ".incomplete").is_file()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test_marker与quarantine符号链接均不移动源目录(tmp_path: Path) -> None:
    source = tmp_path / "bases" / _BASE_ID
    source.mkdir(parents=True)
    external_marker = tmp_path / "external-marker"
    external_marker.write_text("after_marker", encoding="utf-8")
    _symlink(source / ".incomplete", external_marker, directory=False)

    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)
    assert source.is_dir()

    (source / ".incomplete").unlink()
    (source / ".incomplete").write_text("after_marker", encoding="utf-8")
    outside = tmp_path / "outside-quarantine"
    outside.mkdir()
    _symlink(tmp_path / "quarantine", outside, directory=True)
    with pytest.raises(RuntimeStoragePathError, match="符号链接"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)
    assert source.is_dir()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test非普通marker在读取内容前被拒绝(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "bases" / _BASE_ID
    source.mkdir(parents=True)
    (source / ".incomplete").mkdir()

    def must_not_read(_source_descriptor: int) -> str:
        raise AssertionError("非普通 marker 不得进入读取阶段")

    monkeypatch.setattr(isolation, "_read_failure_stage", must_not_read)
    with pytest.raises(RuntimeStoragePathError, match="普通文件"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert source.is_dir()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test移动失败保留未完成树且不递归删除(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_base_json",
    )

    def fail_move(*_args: object) -> None:
        raise OSError("注入 rename 失败")

    monkeypatch.setattr(isolation, "_move_source", fail_move)
    with pytest.raises(OSError, match="注入 rename 失败"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert source.is_dir()
    assert (source / ".incomplete").is_file()
    assert (source / "payload.txt").is_file()
    assert not (tmp_path / "quarantine").exists()
    assert (tmp_path / "journal" / "pending" / f"isolate-base-{_BASE_ID}.json").is_file()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test持久intent尚未移动源目录时恢复只精确删除intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_base_json",
    )
    original_move = isolation._move_source

    def fail_move(*_args: object) -> None:
        raise OSError("注入 intent 后崩溃")

    monkeypatch.setattr(isolation, "_move_source", fail_move)
    with pytest.raises(OSError, match="intent 后崩溃"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    pending = tmp_path / "journal" / "pending" / f"isolate-base-{_BASE_ID}.json"
    assert pending.is_file()
    monkeypatch.setattr(isolation, "_move_source", original_move)

    assert _isolate_incomplete(tmp_path, "base", _BASE_ID) is None
    assert source.is_dir()
    assert (source / "payload.txt").read_text(encoding="utf-8") == "保留半成品"
    assert not pending.exists()
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test隔离目标已存在时不覆盖目标且保留源与intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    destination = (
        tmp_path
        / "quarantine"
        / "incomplete"
        / "base"
        / f"{_BASE_ID}.20260721T000000000000Z.0123456789abcdef"
    )
    destination.mkdir(parents=True)
    sentinel = destination / "sentinel.txt"
    sentinel.write_text("既有隔离对象", encoding="utf-8")
    monkeypatch.setattr(
        isolation,
        "_destination_path",
        lambda *_args: destination,
    )

    with pytest.raises(RuntimeStoragePathError, match="目标已存在"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    pending = tmp_path / "journal" / "pending" / f"isolate-base-{_BASE_ID}.json"
    assert source.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "既有隔离对象"
    assert pending.is_file()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def testintent发布后源inode替换时拒绝移动替换对象(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    previous = source.with_name(f"{_BASE_ID}.previous")
    original_write = isolation._write_intent

    def write_then_replace(path: Path, payload: bytes, root: object) -> None:
        original_write(path, payload, root)
        source.rename(previous)
        source.mkdir()
        (source / ".incomplete").write_text("after_marker", encoding="utf-8")
        (source / "replacement.txt").write_text("替换对象", encoding="utf-8")

    monkeypatch.setattr(isolation, "_write_intent", write_then_replace)

    with pytest.raises(RuntimeStoragePathError, match="移动前发生身份漂移"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert (previous / "payload.txt").read_text(encoding="utf-8") == "保留半成品"
    assert (source / "replacement.txt").read_text(encoding="utf-8") == "替换对象"
    assert (tmp_path / "journal" / "pending" / f"isolate-base-{_BASE_ID}.json").is_file()


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test移动末次根复验后替换根不出现隔离目录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "runtime"
    _incomplete_object(root, "base", _BASE_ID, stage="after_install")
    outside = tmp_path / "outside"
    outside.mkdir()
    previous = tmp_path / "runtime-previous"
    original_move = isolation._move_source

    def move_then_replace(*args: object) -> None:
        original_move(*args)
        root.rename(previous)
        root.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(isolation, "_move_source", move_then_replace)

    with pytest.raises(RuntimeStoragePathError, match="运行时根"):
        _isolate_incomplete(root, "base", _BASE_ID)

    assert not (outside / "quarantine").exists()
    assert len(tuple((previous / "quarantine" / "incomplete" / "base").iterdir())) == 1


@pytest.mark.skipif(not _POSIX, reason="目录 fsync 与隔离只在 WSL/Linux 验证")
def test_rename后崩溃仅恢复一次审计记录(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _incomplete_object(
        tmp_path,
        "base",
        _BASE_ID,
        stage="after_install",
    )
    original_append = isolation._append_record

    def fail_after_rename(_descriptor: int, _record: dict[str, str]) -> None:
        raise OSError("注入审计前崩溃")

    monkeypatch.setattr(isolation, "_append_record", fail_after_rename)
    with pytest.raises(OSError, match="注入审计前崩溃"):
        _isolate_incomplete(tmp_path, "base", _BASE_ID)

    pending = tmp_path / "journal" / "pending" / f"isolate-base-{_BASE_ID}.json"
    assert pending.is_file()
    assert not source.exists()
    quarantined = tuple((tmp_path / "quarantine" / "incomplete" / "base").iterdir())
    assert len(quarantined) == 1

    monkeypatch.setattr(isolation, "_append_record", original_append)
    recovered = _isolate_incomplete(tmp_path, "base", _BASE_ID)

    assert recovered == quarantined[0]
    assert not pending.exists()
    lines = (
        (tmp_path / "journal" / "runtime-storage.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert len(lines) == 1
    assert (
        json.loads(lines[0])["isolation_relative_path"]
        == recovered.relative_to(tmp_path).as_posix()
    )

    assert _isolate_incomplete(tmp_path, "base", _BASE_ID) is None
    assert (
        len(
            (tmp_path / "journal" / "runtime-storage.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        == 1
    )
