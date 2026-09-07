"""File spool 的耐久 JSON、路径、旧 marker 与有界逐 key 锁。"""
from __future__ import annotations

import json
import math
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from uuid import uuid4

from codev_platform.reindex.file_advisory_lock import advisory_lock
from codev_platform.reindex.file_durability import (
    durable_replace,
    durable_unlink,
    fsync_directory as _fsync_directory,
)
from codev_platform.reindex.file_queue_codec import (
    MAX_JSON_BYTES,
    active_payload,
    decode_object,
    meta_from_payload,
    pending_payload_has_no_claim_or_owner,
    pending_version_from_payload,
    pending_payload,
    quarantine_payload,
    result_payload,
    strict_meta_from_payload,
)
from codev_platform.reindex.queue_ports import (
    Job,
    JobMeta,
    QuarantineRecord,
    QueueOperationTimeout,
    validate_timeout,
)

KEY_SEPARATOR = "__"
PENDING = "pending"
ACTIVE = "active"
RESULTS = "results"
QUARANTINED = "quarantined"
LOCKS = "locks"

_JSON_SUFFIX = ".json"
_LOCK_SUFFIX = ".lock"
_LOCK_INFO = "owner.json"
_LOCK_WAIT_SEC = 0.01
_LOCK_RELEASE_TIMEOUT_SEC = 0.2
_DAMAGED_LOCK_GRACE_SEC = 1.0
class _LockAttempt(Enum):
    """gate 内单次尝试的非成功结果。"""

    RETRY = "retry"
    WAIT = "wait"
    UNAVAILABLE = "unavailable"


def _windows_pid_alive(pid: int) -> bool:
    """通过只读进程句柄判断 Windows PID，绝不向目标进程发送信号。"""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() != error_invalid_parameter
    exit_code = wintypes.DWORD()
    try:
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


@dataclass(frozen=True, slots=True)
class OperationDeadline:
    expires_at: float

    @classmethod
    def start(cls, timeout_sec: float) -> OperationDeadline:
        return cls(time.monotonic() + validate_timeout(timeout_sec))

    def remaining(self) -> float:
        return self.expires_at - time.monotonic()

    def check(self) -> None:
        if self.remaining() <= 0:
            raise QueueOperationTimeout("File 队列操作超过时间预算")


@dataclass(frozen=True, slots=True)
class FileQueueRecord:
    phase: str
    path: Path
    project_id: str
    kind: str
    enqueued_at: float
    meta: JobMeta
    claim_token: str | None = None
    owner_token: str | None = None
    lease_expires_at: float | None = None
    result_status: str | None = None
    pending_version: str | None = None
    strict_pending_meta: JobMeta | None = None
    pending_has_no_claim_or_owner: bool = False

    @property
    def key(self) -> str:
        return f"{self.project_id}{KEY_SEPARATOR}{self.kind}"

    def to_job(self, *, include_token: bool = False) -> Job:
        return Job(
            self.project_id,
            self.kind,
            self.enqueued_at,
            token=self.claim_token if include_token else None,
            meta=self.meta,
            lease_expires_at=self.lease_expires_at,
            pending_version=self.pending_version if self.phase == PENDING else None,
            owner_token=self.owner_token if include_token else None,
        )


class FileQueueStore:
    """只提供 File spool 的耐久存取原语，不决定业务状态转换。"""

    def __init__(self, root: Path, *, lock_owner_token: str) -> None:
        self.location = Path(root)
        self._lock_owner_token = lock_owner_token
        self.location.mkdir(parents=True, exist_ok=True)
        for phase in (PENDING, ACTIVE, RESULTS, QUARANTINED, LOCKS):
            self.phase_dir(phase).mkdir(parents=True, exist_ok=True)

    def phase_dir(self, phase: str) -> Path:
        return self.location / phase

    @staticmethod
    def _safe_path(parent: Path, name: str) -> Path | None:
        path = parent / name
        try:
            if path.resolve().parent != parent.resolve():
                return None
        except (OSError, ValueError):
            return None
        return path

    def phase_path(self, phase: str, key: str) -> Path | None:
        return self._safe_path(self.phase_dir(phase), f"{key}{_JSON_SUFFIX}")

    def legacy_path(self, key: str) -> Path | None:
        return self._safe_path(self.location, key)

    def lock_path(self, key: str) -> Path | None:
        return self._safe_path(self.phase_dir(LOCKS), f"{key}{_LOCK_SUFFIX}")

    def read_json(self, path: Path) -> dict[str, object]:
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        return decode_object(raw)

    def write_json_atomic(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_JSON_BYTES:
            raise ValueError("队列 JSON 超过大小上限")
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        owned = False
        try:
            with temp.open("xb") as stream:
                owned = True
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            durable_replace(temp, path)
        finally:
            if owned:
                temp.unlink(missing_ok=True)

    @staticmethod
    def unlink(path: Path) -> bool:
        """耐久删除单个状态文件，并同步其父目录项。"""
        return durable_unlink(path)

    @staticmethod
    def _lock_info_path(path: Path) -> Path:
        return path / _LOCK_INFO

    @staticmethod
    def _pid_alive(pid: object) -> bool:
        if type(pid) is not int or pid <= 0:
            return False
        if os.name == "nt":
            return _windows_pid_alive(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True

    def _lock_payload(self, lock_id: str) -> dict[str, object]:
        return {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "owner_token": self._lock_owner_token,
            "lock_id": lock_id,
            "created_at": time.time(),
        }

    def _read_lock_payload(self, path: Path) -> dict[str, object] | None:
        try:
            return self.read_json(self._lock_info_path(path))
        except (OSError, ValueError):
            return None

    @staticmethod
    def _lock_age(path: Path, payload: dict[str, object] | None) -> float:
        created_at = payload.get("created_at") if payload else None
        if type(created_at) in (int, float):
            return max(0.0, time.time() - float(created_at))
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            return 0.0

    def _lock_is_stale(self, path: Path) -> bool:
        payload = self._read_lock_payload(path)
        valid = bool(
            payload
            and type(payload.get("host")) is str
            and bool(str(payload.get("host")).strip())
            and type(payload.get("pid")) is int
            and int(payload.get("pid", 0)) > 0
            and type(payload.get("owner_token")) is str
            and bool(str(payload.get("owner_token")).strip())
            and type(payload.get("created_at")) in (int, float)
            and math.isfinite(float(payload.get("created_at", 0.0)))
        )
        if not valid:
            return self._lock_age(path, payload) >= _DAMAGED_LOCK_GRACE_SEC
        assert payload is not None
        host = payload.get("host")
        if host != socket.gethostname():
            return False
        pid = payload.get("pid")
        return not self._pid_alive(pid)

    @staticmethod
    def _owned_lock_temp(name: str) -> bool:
        return name.startswith(f".{_LOCK_INFO}.") and name.endswith(".tmp")

    def _clear_lock_dir(self, path: Path) -> bool:
        try:
            for child in path.iterdir():
                if not child.is_file():
                    return False
                if child.name != _LOCK_INFO and not self._owned_lock_temp(child.name):
                    return False
                child.unlink(missing_ok=True)
            path.rmdir()
            _fsync_directory(path.parent)
        except OSError:
            return False
        return True

    @staticmethod
    def _guard_path(path: Path) -> Path:
        return path.with_name(f".{path.name}.gate")

    def _create_owned_lock(
        self,
        path: Path,
        deadline: OperationDeadline,
    ) -> str:
        lock_id = uuid4().hex
        try:
            self.write_json_atomic(
                self._lock_info_path(path), self._lock_payload(lock_id),
            )
            deadline.check()
        except BaseException:
            self._clear_lock_dir(path)
            raise
        return lock_id

    def _existing_lock_attempt(self, path: Path, *, blocking: bool) -> _LockAttempt:
        if self._lock_is_stale(path) and self._clear_lock_dir(path):
            return _LockAttempt.RETRY
        return _LockAttempt.WAIT if blocking else _LockAttempt.UNAVAILABLE

    def _attempt_lock_under_gate(
        self,
        path: Path,
        deadline: OperationDeadline,
        *,
        blocking: bool,
    ) -> str | _LockAttempt:
        with advisory_lock(
            self._guard_path(path), deadline, blocking=blocking,
        ) as guarded:
            if not guarded:
                return _LockAttempt.UNAVAILABLE
            try:
                path.mkdir()
            except FileExistsError:
                return self._existing_lock_attempt(path, blocking=blocking)
            return self._create_owned_lock(path, deadline)

    def _acquire_lock(self, path: Path, deadline: OperationDeadline,
                      *, blocking: bool) -> str | None:
        while True:
            deadline.check()
            outcome = self._attempt_lock_under_gate(
                path, deadline, blocking=blocking,
            )
            if isinstance(outcome, str):
                return outcome
            if outcome is _LockAttempt.UNAVAILABLE:
                return None
            if outcome is _LockAttempt.RETRY:
                continue
            remaining = deadline.remaining()
            if remaining <= 0:
                raise QueueOperationTimeout("File 队列锁等待超时")
            time.sleep(min(_LOCK_WAIT_SEC, remaining))

    def _release_lock(self, path: Path, lock_id: str) -> bool:
        deadline = OperationDeadline.start(_LOCK_RELEASE_TIMEOUT_SEC)
        with advisory_lock(self._guard_path(path), deadline, blocking=True) as guarded:
            if not guarded or not path.exists():
                return guarded
            payload = self._read_lock_payload(path)
            if payload is None or payload.get("lock_id") != lock_id:
                return False
            while not self._clear_lock_dir(path):
                remaining = deadline.remaining()
                if remaining <= 0:
                    return False
                time.sleep(min(_LOCK_WAIT_SEC, remaining))
            return True

    @contextmanager
    def key_lock(self, key: str, deadline: OperationDeadline, *, blocking: bool = True):
        path = self.lock_path(key)
        if path is None:
            raise ValueError(f"非法队列 key: {key!r}")
        lock_id = self._acquire_lock(path, deadline, blocking=blocking)
        acquired = lock_id is not None
        try:
            yield acquired
        finally:
            if lock_id is not None and not self._release_lock(path, lock_id):
                raise RuntimeError("File 队列锁目录释放失败")

    def load_record(self, path: Path, phase: str) -> FileQueueRecord:
        stat = path.stat()
        raw_key = path.stem if phase != "legacy" else path.name
        project_id, _, kind = raw_key.partition(KEY_SEPARATOR)
        data: dict[str, object] = {}
        raw = b""
        if stat.st_size:
            with path.open("rb") as stream:
                raw = stream.read(MAX_JSON_BYTES + 1)
            data = decode_object(raw)
        payload_project = data.get("project_id")
        payload_kind = data.get("kind")
        if payload_project is not None and payload_project != project_id:
            raise ValueError("队列 payload project_id 与文件 key 不一致")
        if payload_kind is not None and payload_kind != kind:
            raise ValueError("队列 payload kind 与文件 key 不一致")
        enqueued = data.get("enqueued_at")
        if enqueued is not None and type(enqueued) not in (int, float):
            raise ValueError("enqueued_at 类型无效")
        if type(enqueued) in (int, float) and not math.isfinite(float(enqueued)):
            raise ValueError("enqueued_at 必须是有限时间")
        enqueued_at = float(enqueued) if type(enqueued) in (int, float) else stat.st_mtime
        meta = meta_from_payload(data.get("meta"), fallback=data)
        claim = data.get("claim_token")
        owner = data.get("owner_token")
        lease = data.get("lease_expires_at")
        result = data.get("result_status")
        if lease is not None and type(lease) not in (int, float):
            raise ValueError("lease_expires_at 类型无效")
        if type(lease) in (int, float) and not math.isfinite(float(lease)):
            raise ValueError("lease_expires_at 必须是有限时间")
        if phase == ACTIVE and (
            type(claim) is not str or not claim.strip()
            or type(lease) not in (int, float)
        ):
            raise ValueError("active 记录缺少 claim 或 lease")
        pending_version = None
        strict_pending_meta = None
        pending_has_no_claim_or_owner = False
        if phase == PENDING:
            pending_version = pending_version_from_payload(data, raw, stat)
            pending_has_no_claim_or_owner = pending_payload_has_no_claim_or_owner(data)
            if "pending_token" in data:
                strict_pending_meta = strict_meta_from_payload(data.get("meta"))
        return FileQueueRecord(
            phase=phase,
            path=path,
            project_id=str(project_id),
            kind=str(kind),
            enqueued_at=enqueued_at,
            meta=meta,
            claim_token=claim if type(claim) is str else None,
            owner_token=owner if type(owner) is str else None,
            lease_expires_at=float(lease) if type(lease) in (int, float) else None,
            result_status=result if type(result) is str else None,
            pending_version=pending_version,
            strict_pending_meta=strict_pending_meta,
            pending_has_no_claim_or_owner=pending_has_no_claim_or_owner,
        )

    def phase_records(self, phase: str) -> list[FileQueueRecord]:
        parent = self.phase_dir(phase)
        records: list[FileQueueRecord] = []
        for path in parent.iterdir():
            if path.is_file() and path.suffix == _JSON_SUFFIX and KEY_SEPARATOR in path.stem:
                try:
                    record = self.load_record(path, phase)
                except (OSError, ValueError):
                    continue
                if record.project_id and record.kind:
                    records.append(record)
        return records

    def legacy_records(self) -> list[FileQueueRecord]:
        records: list[FileQueueRecord] = []
        for path in self.location.iterdir():
            if path.is_file() and KEY_SEPARATOR in path.name:
                try:
                    records.append(self.load_record(path, "legacy"))
                except (OSError, ValueError):
                    continue
        return records

    def read_phase(self, phase: str, key: str) -> FileQueueRecord | None:
        path = self.phase_path(phase, key)
        if path is None or not path.exists():
            return None
        return self.load_record(path, phase)

    def read_legacy(self, key: str) -> FileQueueRecord | None:
        path = self.legacy_path(key)
        if path is None or not path.exists():
            return None
        return self.load_record(path, "legacy")

    def read_quarantine(self, key: str) -> QuarantineRecord | None:
        path = self.phase_path(QUARANTINED, key)
        if path is None or not path.exists():
            return None
        data = self.read_json(path)
        expected = {
            "project_id", "kind", "claim_token", "attempt_id", "fence",
            "process_identity", "containment_kind", "native_ref", "reason",
            "quarantined_at",
        }
        if set(data) != expected:
            raise ValueError("quarantine 记录字段集合无效")
        return QuarantineRecord(**data)  # type: ignore[arg-type]

    @staticmethod
    def sorted_records(records: list[FileQueueRecord]) -> list[FileQueueRecord]:
        from codev_platform.reindex.runners import kinds

        rank = {kind: index for index, kind in enumerate(kinds())}
        unknown = len(rank)
        return sorted(records, key=lambda item: (
            item.enqueued_at, rank.get(item.kind, unknown), item.key,
        ))

    def migrate_legacy_locked(self, key: str) -> None:
        legacy = self.read_legacy(key)
        if legacy is None:
            return
        pending = self.read_phase(PENDING, key)
        if pending is None or legacy.enqueued_at >= pending.enqueued_at:
            path = self.phase_path(PENDING, key)
            if path is not None:
                self.write_json_atomic(path, pending_payload(legacy))
        self.unlink(legacy.path)

    def migrate_legacy(self, deadline: OperationDeadline, *, blocking: bool) -> None:
        for legacy in self.legacy_records():
            with self.key_lock(legacy.key, deadline, blocking=blocking) as acquired:
                if acquired:
                    self.migrate_legacy_locked(legacy.key)


__all__ = [
    "ACTIVE", "FileQueueRecord", "FileQueueStore", "KEY_SEPARATOR",
    "OperationDeadline", "PENDING", "QUARANTINED", "RESULTS",
    "active_payload", "pending_payload",
    "quarantine_payload", "result_payload",
]
