"""同一受绑定根租约内的半成品隔离恢复事务。"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import codev_platform.runtime_storage as storage
from codev_platform._runtime_renameat2 import rename_noreplace_at
from codev_platform.runtime_managed_file import (
    ManagedFileError,
    ManagedFilePolicy,
    create_managed_bytes_exclusive_at,
    open_managed_directory_descriptor_at,
    open_managed_regular_descriptor_at,
    open_optional_managed_directory_descriptor_at,
    read_optional_managed_bytes_at,
    remove_managed_bytes_exact_at,
)
from codev_platform.runtime_object_lock_capability import (
    BoundRuntimeObjectLock,
    RuntimeObjectLockCapabilityError,
)
from codev_platform.runtime_root_binding import BoundRuntimeRoot, RuntimeRootBindingError


_METADATA_NAMES = {"base": "base.json", "release": "release.json"}
_FAILURE_STAGES = frozenset(
    {
        "after_marker",
        "after_venv",
        "after_install",
        "after_base_json",
        "after_release_json",
        "completed_corrupt",
        "marker_missing",
    }
)
_RECORD_FIELDS = frozenset(
    {"failure_stage", "id", "isolated_at", "isolation_relative_path", "kind"}
)
_INTENT_FIELDS = frozenset({"record", "source_relative_path", "version"})
_ISOLATED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_DESTINATION_SUFFIX = re.compile(r"\d{8}T\d{12}Z\.[0-9a-f]{16}\Z")
_INTENT_MAX_BYTES = 8192
_JOURNAL_MODE = 0o644
_JOURNAL_ALLOWED_EXISTING_MODES = frozenset({0o600, 0o640, _JOURNAL_MODE})
_INTENT_MODE = 0o600


@dataclass(frozen=True, slots=True)
class _SourceObservation:
    """一次严格 source 读取得到的故障阶段与目录身份。"""

    failure_stage: str | None
    device: int
    inode: int


def isolate_incomplete_locked_at(
    lock: BoundRuntimeObjectLock,
    kind: str,
    object_id: str,
) -> Path | None:
    """仅在调用方仍持同一对象的活动排他锁时隔离未完成对象。"""
    return _isolate_object(
        lock,
        kind,
        object_id,
        completed_failure_stage=None,
    )


def isolate_corrupt_completed_locked_at(
    lock: BoundRuntimeObjectLock,
    kind: str,
    object_id: str,
) -> Path | None:
    """仅在调用方仍持同一对象的活动排他锁时隔离损坏完成对象。"""
    return _isolate_object(
        lock,
        kind,
        object_id,
        completed_failure_stage="completed_corrupt",
    )


def _isolate_object(
    lock: BoundRuntimeObjectLock,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> Path | None:
    try:
        with lock.hold_active(kind=kind, object_id=object_id, exclusive=True) as root:
            return _isolate_bound_root(
                root,
                kind,
                object_id,
                completed_failure_stage=completed_failure_stage,
            )
    except RuntimeObjectLockCapabilityError as error:
        raise storage.RuntimeStoragePathError(f"对象锁 capability 无效：{error}") from None


def _isolate_bound_root(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> Path | None:
    try:
        root.verify_visible()
        resolved_kind, resolved_id = storage._identity(kind, object_id)
        intent_path = _intent_path(root, resolved_kind, resolved_id)
        intent_payload = _read_intent_or_none(intent_path, root)
        source = _observe_source(
            root,
            resolved_kind,
            resolved_id,
            completed_failure_stage=completed_failure_stage,
        )
        if intent_payload is None and (source is None or source.failure_stage is None):
            return None
        with storage.isolation_journal_lock_at(root):
            return _isolate_under_journal_lock(
                root,
                resolved_kind,
                resolved_id,
                completed_failure_stage=completed_failure_stage,
            )
    except RuntimeRootBindingError as error:
        raise storage.RuntimeStoragePathError(
            "运行时根目录身份或可见路径已漂移",
        ) from error
    except ManagedFileError as error:
        raise storage.RuntimeStoragePathError(str(error)) from None


def _isolate_under_journal_lock(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> Path | None:
    journal_path = root.path / "journal" / "runtime-storage.jsonl"
    journal, _created = _open_journal(journal_path, root)
    try:
        _read_journal_paths(journal)
        intent_path = _intent_path(root, kind, object_id)
        intent_payload = _read_intent_or_none(intent_path, root)
        if intent_payload is not None:
            return _recover_intent(
                intent_path,
                intent_payload,
                root,
                kind,
                object_id,
                journal,
            )
        source = _observe_source(
            root,
            kind,
            object_id,
            completed_failure_stage=completed_failure_stage,
        )
        if source is None or source.failure_stage is None:
            return None
        category = "corrupt" if completed_failure_stage is not None else "incomplete"
        now = datetime.now(timezone.utc)
        destination = _destination_path(root, category, kind, object_id, now)
        record = {
            "failure_stage": source.failure_stage,
            "id": object_id,
            "isolated_at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "isolation_relative_path": destination.relative_to(root.path).as_posix(),
            "kind": kind,
        }
        intent = {
            "record": record,
            "source_relative_path": f"{storage._KINDS[kind]}/{object_id}",
            "version": 1,
        }
        payload = _canonical_json(intent)
        _write_intent(intent_path, payload, root)
        _move_source(root, kind, object_id, destination, source)
        _append_record(journal, record)
        _remove_intent(intent_path, payload, root)
        return destination
    finally:
        os.close(journal)


def _observe_source(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    *,
    completed_failure_stage: str | None,
) -> _SourceObservation | None:
    descriptor = _open_source_or_none(root, kind, object_id)
    if descriptor is None:
        return None
    try:
        metadata = os.fstat(descriptor)
        return _SourceObservation(
            failure_stage=_classify_source(
                descriptor,
                kind,
                completed_failure_stage=completed_failure_stage,
            ),
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
    finally:
        os.close(descriptor)


def _open_source_or_none(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
) -> int | None:
    return open_optional_managed_directory_descriptor_at(
        root.path / storage._KINDS[kind] / object_id,
        root=root,
    )


def _classify_source(
    source_descriptor: int,
    kind: str,
    *,
    completed_failure_stage: str | None,
) -> str | None:
    marker = _stat_optional(".incomplete", source_descriptor)
    if marker is None:
        metadata = _stat_optional(_METADATA_NAMES[kind], source_descriptor)
        if metadata is None:
            return "marker_missing"
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            if completed_failure_stage is not None:
                return completed_failure_stage
            raise storage.RuntimeStoragePathError("完成元数据必须是普通文件")
        return completed_failure_stage
    if stat.S_ISLNK(marker.st_mode):
        raise storage.RuntimeStoragePathError(".incomplete 不能是符号链接")
    if not stat.S_ISREG(marker.st_mode):
        raise storage.RuntimeStoragePathError(".incomplete 必须是普通文件")
    return _read_failure_stage(source_descriptor)


def _stat_optional(name: str, parent_descriptor: int) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise storage.RuntimeStoragePathError("隔离对象无法安全检查") from error


def _read_failure_stage(source_descriptor: int) -> str:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(".incomplete", flags, dir_fd=source_descriptor)
    except OSError as error:
        raise storage.RuntimeStoragePathError(".incomplete 不可安全读取") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise storage.RuntimeStoragePathError(".incomplete 必须是普通文件")
        raw = os.read(descriptor, 257)
    finally:
        os.close(descriptor)
    try:
        stage = raw.decode("utf-8").strip() if len(raw) <= 256 else "unknown"
    except UnicodeDecodeError:
        stage = "unknown"
    return stage if stage in _FAILURE_STAGES else "unknown"


def _intent_path(root: BoundRuntimeRoot, kind: str, object_id: str) -> Path:
    return root.path / "journal" / "pending" / f"isolate-{kind}-{object_id}.json"


def _intent_policy(root: BoundRuntimeRoot) -> ManagedFilePolicy:
    return ManagedFilePolicy(
        mode=_INTENT_MODE,
        require_uid=root.owner_uid,
        max_bytes=_INTENT_MAX_BYTES,
    )


def _read_intent_or_none(path: Path, root: BoundRuntimeRoot) -> bytes | None:
    return read_optional_managed_bytes_at(
        path,
        root=root,
        policy=_intent_policy(root),
        allow_missing_parent=True,
    )


def _write_intent(path: Path, payload: bytes, root: BoundRuntimeRoot) -> None:
    try:
        create_managed_bytes_exclusive_at(
            path,
            payload,
            root=root,
            policy=_intent_policy(root),
        )
    except FileExistsError:
        raise storage.RuntimeStoragePathError("隔离意图已存在") from None


def _remove_intent(path: Path, payload: bytes, root: BoundRuntimeRoot) -> None:
    if not remove_managed_bytes_exact_at(
        path,
        payload,
        root=root,
        policy=_intent_policy(root),
    ):
        raise storage.RuntimeStoragePathError("隔离意图在精确删除前缺失")


def _open_journal(path: Path, root: BoundRuntimeRoot) -> tuple[int, bool]:
    return open_managed_regular_descriptor_at(
        path,
        root=root,
        mode=_JOURNAL_MODE,
        read_only=False,
        read_write=True,
        append=True,
        create_missing=True,
        allowed_existing_modes=_JOURNAL_ALLOWED_EXISTING_MODES,
    )


def _read_journal_paths(descriptor: int) -> set[str]:
    size = os.fstat(descriptor).st_size
    os.lseek(descriptor, 0, os.SEEK_SET)
    raw = bytearray()
    while len(raw) < size:
        chunk = os.read(descriptor, min(65536, size - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    if raw and not raw.endswith(b"\n"):
        committed_size = raw.rfind(b"\n") + 1
        os.ftruncate(descriptor, committed_size)
        os.fsync(descriptor)
        del raw[committed_size:]
    paths: set[str] = set()
    for line in bytes(raw).splitlines():
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise storage.RuntimeStoragePathError("隔离审计包含损坏记录") from error
        paths.add(_validated_record(record)["isolation_relative_path"])
    return paths


def _append_record(descriptor: int, record: dict[str, str]) -> None:
    original_size = os.fstat(descriptor).st_size
    payload = _canonical_json(record) + b"\n"
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    except BaseException:
        try:
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
        except OSError:
            pass
        raise


def _recover_intent(
    path: Path,
    payload: bytes,
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    journal: int,
) -> Path | None:
    _source, destination, record = _decode_intent(payload, root.path, kind, object_id)
    source_exists = _source_exists(root, kind, object_id)
    destination_descriptor = open_optional_managed_directory_descriptor_at(
        destination,
        root=root,
    )
    destination_exists = destination_descriptor is not None
    if destination_descriptor is not None:
        os.close(destination_descriptor)
    if source_exists and not destination_exists:
        _remove_intent(path, payload, root)
        return None
    if not source_exists and destination_exists:
        if record["isolation_relative_path"] not in _read_journal_paths(journal):
            _append_record(journal, record)
        else:
            os.fsync(journal)
        _remove_intent(path, payload, root)
        return destination
    raise storage.RuntimeStoragePathError("隔离意图对应的目录状态不可证明")


def _source_exists(root: BoundRuntimeRoot, kind: str, object_id: str) -> bool:
    descriptor = _open_source_or_none(root, kind, object_id)
    if descriptor is None:
        return False
    os.close(descriptor)
    return True


def _decode_intent(
    payload: bytes,
    root: Path,
    kind: str,
    object_id: str,
) -> tuple[Path, Path, dict[str, str]]:
    try:
        value: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise storage.RuntimeStoragePathError("隔离意图不可恢复") from error
    if not isinstance(value, dict) or set(value) != _INTENT_FIELDS or value.get("version") != 1:
        raise storage.RuntimeStoragePathError("隔离意图结构无效")
    source_relative = value.get("source_relative_path")
    if type(source_relative) is not str:
        raise storage.RuntimeStoragePathError("隔离意图源路径无效")
    record = _validated_record(value.get("record"))
    if record["kind"] != kind or record["id"] != object_id:
        raise storage.RuntimeStoragePathError("隔离意图身份不匹配")
    source = storage._inside(root, root / source_relative)
    destination = storage._inside(root, root / record["isolation_relative_path"])
    expected_source = root / storage._KINDS[kind] / object_id
    category = "corrupt" if record["failure_stage"] == "completed_corrupt" else "incomplete"
    if source != expected_source or destination.parent != root / "quarantine" / category / kind:
        raise storage.RuntimeStoragePathError("隔离意图路径越界")
    return source, destination, record


def _move_source(
    root: BoundRuntimeRoot,
    kind: str,
    object_id: str,
    destination: Path,
    source: _SourceObservation,
) -> None:
    source_parent: int | None = None
    destination_parent: int | None = None
    try:
        source_parent = open_managed_directory_descriptor_at(
            root.path / storage._KINDS[kind],
            root=root,
            create_missing=False,
        )
        destination_parent = open_managed_directory_descriptor_at(
            destination.parent,
            root=root,
            create_missing=True,
        )
        current = os.stat(object_id, dir_fd=source_parent, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
            source.device,
            source.inode,
        ):
            raise storage.RuntimeStoragePathError("隔离源在移动前发生身份漂移")
        root.verify_visible()
        rename_noreplace_at(
            object_id,
            destination.name,
            source_parent,
            destination_parent,
        )
        os.fsync(source_parent)
        os.fsync(destination_parent)
        root.verify_visible()
    except FileExistsError:
        raise storage.RuntimeStoragePathError("隔离目标已存在") from None
    finally:
        if destination_parent is not None:
            os.close(destination_parent)
        if source_parent is not None:
            os.close(source_parent)


def _destination_path(
    root: BoundRuntimeRoot,
    category: str,
    kind: str,
    object_id: str,
    now: datetime,
) -> Path:
    suffix = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}.{secrets.token_hex(8)}"
    return root.path / "quarantine" / category / kind / f"{object_id}.{suffix}"


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("审计写入未取得进展")
        offset += written


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validated_record(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _RECORD_FIELDS:
        raise storage.RuntimeStoragePathError("隔离审计记录结构无效")
    if any(type(item) is not str for item in value.values()):
        raise storage.RuntimeStoragePathError("隔离审计记录字段无效")
    record = dict(value)
    kind, object_id = storage._identity(record["kind"], record["id"])
    stage = record["failure_stage"]
    if stage not in _FAILURE_STAGES or _ISOLATED_AT.fullmatch(record["isolated_at"]) is None:
        raise storage.RuntimeStoragePathError("隔离审计阶段或时间无效")
    try:
        datetime.strptime(record["isolated_at"], "%Y-%m-%dT%H:%M:%S.%fZ")
        relative = PurePosixPath(record["isolation_relative_path"])
    except (TypeError, ValueError):
        raise storage.RuntimeStoragePathError("隔离审计时间或路径无效") from None
    category = "corrupt" if stage == "completed_corrupt" else "incomplete"
    parts = relative.parts
    prefix = f"{object_id}."
    if (
        relative.is_absolute()
        or len(parts) != 4
        or parts[:3] != ("quarantine", category, kind)
        or not parts[3].startswith(prefix)
        or _DESTINATION_SUFFIX.fullmatch(parts[3].removeprefix(prefix)) is None
    ):
        raise storage.RuntimeStoragePathError("隔离审计路径无效")
    return record


__all__ = [
    "isolate_corrupt_completed_locked_at",
    "isolate_incomplete_locked_at",
]
