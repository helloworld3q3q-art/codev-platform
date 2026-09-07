"""reindex worker 状态、心跳与单写运行锁。"""
from __future__ import annotations

import json
import os
import hashlib
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from codev_platform.core.paths import data_root
from codev_platform.reindex.maintenance_gate import (
    activate_maintenance_gate as activate_maintenance_gate,
    deactivate_maintenance_gate as deactivate_maintenance_gate,
    maintenance_gate_active as maintenance_gate_active,
)
from codev_platform.reindex.worker_launcher import (
    WorkerLauncherPorts as _WorkerLauncherPorts,
    WorkerSettings as WorkerSettings,
    acquire_start_lock as _acquire_start_lock,
    ensure_worker_running as _ensure_worker_running,
    should_auto_start as should_auto_start,
    spawn_worker_process as _spawn_worker_process,
    start_lock_path as _launcher_start_lock_path,
    worker_settings as worker_settings,
)
from codev_platform.reindex.supervisor_process_identity import (
    LockProcessState,
    identity_matches as _identity_matches,
    is_pid_running as is_pid_running,
    process_birth_identity as _native_process_birth_identity,
    probe_lock_process_state as _probe_lock_process_state,
    stored_pid as _stored_pid,
    verification_fields as _verification_fields,
)
from codev_platform.reindex.run_lock_protocol import (
    RunLockDisposition,
    RunLockUnavailableError,
    acquire as _acquire_run_lock_file,
    inspect as _inspect_run_lock_file,
    read_lock_payload as _read_run_lock_payload,
    reclaim_stale as _reclaim_stale_run_lock,
    release as _release_run_lock_file,
)

_STATE_FILE = "reindex-worker-state.json"
_RUN_LOCK = "reindex-worker-run.lock"
_STATE_SCHEMA_VERSION = 3
_RUN_LOCK_SCHEMA_VERSION = 1
_EXECUTION_MODES = {"isolated", "legacy"}
_PHASES = {
    "queue_scan",
    "git_sync",
    "runner",
    "manifest",
    "queue_complete",
    "health_refresh",
    "idle",
    "stopping",
    "stopped",
    "starting",
}


def new_owner_token() -> str:
    return uuid4().hex


def runtime_dir() -> Path:
    d = data_root() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return runtime_dir() / _STATE_FILE


def _start_lock_path() -> Path:
    return _launcher_start_lock_path(runtime_dir())


def _run_lock_path() -> Path:
    return runtime_dir() / _RUN_LOCK


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_state_file() -> dict[str, Any]:
    return _read_json(state_path())


def _write_state(data: dict[str, Any]) -> None:
    _write_json(state_path(), data)


def _patch_state(owner_token: str, **updates: Any) -> dict[str, Any]:
    data = _read_state_file()
    existing = data.get("owner_token")
    if existing and existing != owner_token:
        return data
    data.update(updates)
    data["owner_token"] = owner_token
    _write_state(data)
    return data


def _hash_command(parts: list[str] | tuple[str, ...] | None) -> str | None:
    if not parts:
        return None
    text = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _process_birth_identity(pid: int) -> str | None:
    """返回 PID 不可复用的出生身份；未支持平台明确返回 None。"""
    return _native_process_birth_identity(pid, platform_name=sys.platform)


def _process_verification_fields(pid: int) -> dict[str, Any]:
    return _verification_fields(pid, birth_identity=_process_birth_identity)


def _process_identity_matches(data: dict[str, Any], pid: int | None = None) -> bool:
    return _identity_matches(
        data,
        is_running=is_pid_running,
        birth_identity=_process_birth_identity,
        pid=pid,
    )


def _lock_process_is_running(lock: dict[str, Any]) -> bool:
    return _run_lock_process_state(lock) is LockProcessState.ACTIVE


def _run_lock_process_state(lock: dict[str, Any]) -> LockProcessState:
    """删除运行锁前使用严格三态探针，未知状态绝不降级为 stale。"""
    return _probe_lock_process_state(
        lock,
        schema_version=_RUN_LOCK_SCHEMA_VERSION,
        platform_name=sys.platform,
    )


def _run_lock_disposition(lock: dict[str, Any]) -> RunLockDisposition:
    state = _run_lock_process_state(lock)
    if state is LockProcessState.ACTIVE:
        return RunLockDisposition.ACTIVE
    if state is LockProcessState.STALE:
        return RunLockDisposition.RECLAIMABLE
    return RunLockDisposition.UNKNOWN


def _process_identity(*, pid: int, executable: str | None,
                      cmd: list[str] | tuple[str, ...] | None,
                      now: float) -> dict[str, Any]:
    return {
        "pid": pid,
        "process_started_at": now,
        "process_executable": executable,
        "process_cmd_hash": _hash_command(cmd),
        **_process_verification_fields(pid),
    }


def _job_fields(job: Any) -> dict[str, Any]:
    project_id = getattr(job, "project_id", None)
    kind = getattr(job, "kind", None)
    if project_id and kind:
        return {
            "active_job": f"{project_id}__{kind}",
            "active_job_project_id": project_id,
            "active_job_kind": kind,
            "last_job": f"{project_id}__{kind}",
        }
    return {
        "active_job": None,
        "active_job_project_id": None,
        "active_job_kind": None,
    }


def _clear_active_job_fields() -> dict[str, Any]:
    return {
        "active_job": None,
        "active_job_project_id": None,
        "active_job_kind": None,
    }


def _base_state(owner_token: str, *, status: str, mode: str,
                idle_exit_sec: float | None, heartbeat_sec: float | None,
                pid: int, executable: str | None,
                cmd: list[str] | tuple[str, ...] | None,
                cwd: str | None = None, execution_mode: str = "legacy") -> dict[str, Any]:
    if execution_mode not in _EXECUTION_MODES:
        raise ValueError("execution_mode 必须是 isolated 或 legacy")
    now = time.time()
    data = {
        "state_schema_version": _STATE_SCHEMA_VERSION,
        "owner_token": owner_token,
        "status": status,
        "mode": mode,
        "execution_mode": execution_mode,
        "started_at": now,
        "heartbeat_at": now,
        "idle_exit_sec": idle_exit_sec,
        "heartbeat_sec": heartbeat_sec,
        "phase": "starting" if status == "starting" else "idle",
        "phase_at": now,
        "last_result": None,
        "last_result_at": None,
        "manifest_ok": True,
        "health_failed": False,
        **_clear_active_job_fields(),
        **_process_identity(pid=pid, executable=executable, cmd=cmd, now=now),
    }
    if cwd is not None:
        data["cwd"] = cwd
    return data


def _normalize_result(result: Any) -> dict[str, Any]:
    if result is None:
        return {}
    if is_dataclass(result):
        payload = asdict(result)
    elif isinstance(result, dict):
        payload = dict(result)
    else:
        payload = dict(getattr(result, "__dict__", {}))
    project_id = payload.get("project_id")
    kind = payload.get("kind")
    job_key = payload.get("job_key")
    if not job_key and project_id and kind:
        job_key = f"{project_id}__{kind}"
    return {
        "job_key": job_key,
        "project_id": project_id,
        "kind": kind,
        "status": payload.get("status"),
        "detail": payload.get("detail") or payload.get("note"),
        "note": payload.get("note") or payload.get("detail"),
        "manifest_ok": payload.get("manifest_ok"),
        "health_failed": payload.get("health_failed"),
    }


def _lock_state() -> dict[str, Any]:
    return _read_run_lock_payload(_run_lock_path())


def _lock_running() -> bool:
    try:
        disposition = _inspect_run_lock_file(
            _run_lock_path(),
            lock_assessment=_run_lock_disposition,
        )
    except RunLockUnavailableError:
        return True
    return disposition is not RunLockDisposition.RECLAIMABLE


def worker_status(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    data = _read_state_file()
    pid = _stored_pid(data)
    running = False
    lock = _lock_state()
    lock_verified = _lock_process_is_running(lock)
    state_verified = (
        data.get("state_schema_version") == _STATE_SCHEMA_VERSION
        and _process_identity_matches(data)
    )
    if lock_verified:
        data = _merge_verified_lock_state(data, lock)
        pid = _stored_pid(lock)
        running = True
    elif data.get("status") == "starting" and state_verified:
        running = True
    heartbeat_at = data.get("heartbeat_at")
    age = None
    if isinstance(heartbeat_at, (int, float)):
        age = max(0, int(now - heartbeat_at))
    data.update({
        "pid": pid or None,
        "running": running,
        "heartbeat_age_sec": age,
        "state_path": str(state_path()),
        "process_verified": lock_verified or state_verified,
    })
    return data


def _merge_verified_lock_state(
    state: dict[str, Any],
    lock: dict[str, Any],
) -> dict[str, Any]:
    """锁只证明唯一持有者，健康心跳仍以同 owner 的状态文件为准。"""
    state_heartbeat = state.get("heartbeat_at")
    merged = {**state, **lock, "status": "running"}
    same_owner = state.get("owner_token") == lock.get("owner_token")
    same_process = _process_identity_matches(state, _stored_pid(lock))
    if same_owner and same_process and isinstance(state_heartbeat, (int, float)):
        merged["heartbeat_at"] = state_heartbeat
    return merged


def _run_lock_payload(owner_token: str) -> dict[str, Any] | None:
    identity = _process_birth_identity(os.getpid())
    if identity is None:
        return None
    return {
        "lock_schema_version": _RUN_LOCK_SCHEMA_VERSION,
        "pid": os.getpid(),
        "owner_token": owner_token,
        "locked_at": time.time(),
        "heartbeat_at": time.time(),
        "process_birth_identity": identity,
    }


def _reclaim_run_lock(path: Path) -> bool:
    return _reclaim_stale_run_lock(path, lock_assessment=_run_lock_disposition)


def _release_run_lock(path: Path, owner_token: str) -> None:
    _release_run_lock_file(
        path,
        owner_token,
        owned_by_current_process=lambda lock: _process_identity_matches(
            lock,
            os.getpid(),
        ),
    )


@contextmanager
def acquire_run_lock(owner_token: str) -> Iterator[bool]:
    path = _run_lock_path()
    payload = _run_lock_payload(owner_token)
    if payload is None:
        raise RunLockUnavailableError("当前进程出生身份无法证明")
    acquired = _acquire_run_lock_file(
        path,
        payload,
        lock_assessment=_run_lock_disposition,
    )
    if not acquired:
        yield False
        return
    try:
        yield True
    except BaseException:
        try:
            _release_run_lock(path, owner_token)
        except (KeyboardInterrupt, SystemExit, MemoryError):
            raise
        except RunLockUnavailableError:
            pass
        raise
    else:
        _release_run_lock(path, owner_token)


def record_worker_start(owner_token: str, *, mode: str,
                        idle_exit_sec: float | None = None,
                        heartbeat_sec: float | None = None,
                        execution_mode: str = "legacy") -> None:
    _write_state(_base_state(
        owner_token,
        status="running",
        mode=mode,
        idle_exit_sec=idle_exit_sec,
        heartbeat_sec=heartbeat_sec,
        pid=os.getpid(),
        executable=sys.executable,
        cmd=list(sys.argv),
        execution_mode=execution_mode,
    ))


def record_spawned(pid: int, owner_token: str, *, cwd: str | None,
                   idle_exit_sec: float, heartbeat_sec: float,
                   cmd: list[str] | None = None,
                   execution_mode: str = "legacy") -> None:
    executable = cmd[0] if cmd else None
    _write_state(_base_state(
        owner_token,
        status="starting",
        mode="short",
        idle_exit_sec=idle_exit_sec,
        heartbeat_sec=heartbeat_sec,
        pid=pid,
        executable=executable,
        cmd=cmd,
        cwd=cwd,
        execution_mode=execution_mode,
    ))


def record_heartbeat(owner_token: str) -> None:
    now = time.time()
    _patch_state(
        owner_token,
        pid=os.getpid(),
        status="running",
        heartbeat_at=now,
        **_process_verification_fields(os.getpid()),
    )


def record_phase(owner_token: str, phase: str, job: Any = None,
                 detail: str | None = None) -> None:
    now = time.time()
    updates: dict[str, Any] = {
        "pid": os.getpid(),
        "status": "stopped" if phase == "stopped" else "running",
        "heartbeat_at": now,
        "phase": phase,
        "phase_at": now,
        **_process_verification_fields(os.getpid()),
    }
    if job is not None:
        updates.update(_job_fields(job))
        updates["last_job_at"] = now
    elif phase in {"idle", "health_refresh", "stopping", "stopped"}:
        updates.update(_clear_active_job_fields())
    if phase == "health_refresh":
        updates["health_failed"] = False
    if detail is not None:
        updates["phase_detail"] = detail
    _patch_state(owner_token, **updates)


def record_result(owner_token: str, result: Any) -> None:
    payload = _normalize_result(result)
    now = time.time()
    updates: dict[str, Any] = {
        "pid": os.getpid(),
        "status": "running",
        "heartbeat_at": now,
        "last_result": payload,
        "last_result_at": now,
        **_process_verification_fields(os.getpid()),
    }
    if payload.get("job_key"):
        updates["last_job"] = payload["job_key"]
        updates["last_job_status"] = payload.get("status")
        updates["last_job_at"] = now
    if payload.get("manifest_ok") is not None:
        updates["manifest_ok"] = bool(payload["manifest_ok"])
    if payload.get("health_failed") is not None:
        updates["health_failed"] = bool(payload["health_failed"])
    _patch_state(owner_token, **updates)


def record_job_event(owner_token: str, job: Any, status: str) -> None:
    if status in _PHASES:
        record_phase(owner_token, status, job=job)
        return
    if status == "manifest_failed":
        _patch_state(owner_token, heartbeat_at=time.time(), manifest_ok=False)
        return
    if status == "health_refresh_failed":
        _patch_state(owner_token, heartbeat_at=time.time(), health_failed=True)
        return
    if status == "result":
        record_result(owner_token, job)
        return
    if status == "running":
        record_phase(owner_token, "runner", job=job)
        return
    if status in {"ok", "ok_dirty", "failed", "retry", "blocked", "exception"}:
        record_result(owner_token, {
            "job_key": getattr(job, "key", None),
            "project_id": getattr(job, "project_id", None),
            "kind": getattr(job, "kind", None),
            "status": "ok" if status in {"ok", "ok_dirty"} else status,
        })
        return
    if status.startswith("discarded"):
        record_result(owner_token, {
            "job_key": getattr(job, "key", None),
            "project_id": getattr(job, "project_id", None),
            "kind": getattr(job, "kind", None),
            "status": "discarded",
            "detail": status,
        })
        return


def record_worker_exit(owner_token: str, reason: str, note: str = "") -> None:
    record_phase(owner_token, "stopping", detail=reason)
    now = time.time()
    _patch_state(
        owner_token,
        pid=os.getpid(),
        status="stopped",
        heartbeat_at=now,
        exited_at=now,
        exit_reason=reason,
        exit_note=note,
        phase="stopped",
        phase_at=now,
        **_process_verification_fields(os.getpid()),
        **_clear_active_job_fields(),
    )


class HeartbeatThread:
    def __init__(self, owner_token: str, interval_sec: float) -> None:
        self._owner_token = owner_token
        self._interval = max(float(interval_sec), 0.01)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="reindex-heartbeat", daemon=True)

    def start(self) -> None:
        record_heartbeat(self._owner_token)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            record_heartbeat(self._owner_token)


@contextmanager
def heartbeat_thread(owner_token: str, interval_sec: float) -> Iterator[None]:
    hb = HeartbeatThread(owner_token, interval_sec)
    hb.start()
    try:
        yield
    finally:
        hb.stop()


@contextmanager
def _start_lock() -> Iterator[bool]:
    with _acquire_start_lock(_start_lock_path()) as acquired:
        yield acquired


def _launcher_ports() -> _WorkerLauncherPorts:
    return _WorkerLauncherPorts(
        worker_status=worker_status,
        run_lock_running=_lock_running,
        start_lock=_start_lock,
        spawn_process=_spawn_worker_process,
        new_owner_token=new_owner_token,
        record_spawned=record_spawned,
    )


def ensure_worker_running(cfg: dict | None = None, *, cwd: str | Path | None = None,
                          queue: object | None = None, idle_exit_sec: float | None = None,
                          heartbeat_sec: float | None = None) -> dict[str, Any]:
    """兼容入口：以当前 supervisor 全局组装 launcher 窄依赖。"""

    return _ensure_worker_running(
        cfg,
        ports=_launcher_ports(),
        cwd=cwd,
        queue=queue,
        idle_exit_sec=idle_exit_sec,
        heartbeat_sec=heartbeat_sec,
    )
