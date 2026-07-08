"""reindex worker lifecycle helpers.

Queue semantics stay in JobQueue/ReindexWorker. This module only owns process
lifecycle metadata and single-writer guards for local operations.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from codev_platform.core.config import get as _cfg_get, load_config
from codev_platform.core.paths import data_root, logs_dir
from codev_platform.reindex.queue import FileSpoolQueue, JobQueue

_STATE_FILE = "reindex-worker-state.json"
_START_LOCK = "reindex-worker-start.lock"
_RUN_LOCK = "reindex-worker-run.lock"
_START_LOCK_STALE_SEC = 60.0
_DEFAULT_IDLE_EXIT_SEC = 600.0
_DEFAULT_HEARTBEAT_SEC = 10.0


@dataclass(frozen=True)
class WorkerSettings:
    auto_start: bool
    auto_start_pg: bool
    idle_exit_sec: float
    heartbeat_sec: float


def new_owner_token() -> str:
    return uuid4().hex


def runtime_dir() -> Path:
    d = data_root() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path() -> Path:
    return runtime_dir() / _STATE_FILE


def _start_lock_path() -> Path:
    return runtime_dir() / _START_LOCK


def _run_lock_path() -> Path:
    return runtime_dir() / _RUN_LOCK


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def worker_settings(cfg: dict | None = None) -> WorkerSettings:
    cfg = cfg if cfg is not None else load_config()
    return WorkerSettings(
        auto_start=_as_bool(_cfg_get(cfg, "reindex.worker_auto_start"), True),
        auto_start_pg=_as_bool(_cfg_get(cfg, "reindex.worker_auto_start_pg"), False),
        idle_exit_sec=_positive_float(
            _cfg_get(cfg, "reindex.worker_idle_exit_sec"), _DEFAULT_IDLE_EXIT_SEC),
        heartbeat_sec=_positive_float(
            _cfg_get(cfg, "reindex.worker_heartbeat_sec"), _DEFAULT_HEARTBEAT_SEC),
    )


def should_auto_start(queue: JobQueue, cfg: dict | None = None) -> bool:
    settings = worker_settings(cfg)
    if not settings.auto_start:
        return False
    if isinstance(queue, FileSpoolQueue):
        return True
    return settings.auto_start_pg


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


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _is_pid_running_windows(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _is_pid_running_windows(pid: int) -> bool:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def _lock_state() -> dict[str, Any]:
    return _read_json(_run_lock_path())


def _lock_running() -> bool:
    pid = int(_lock_state().get("pid") or 0)
    return is_pid_running(pid) if pid else False


def worker_status(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    data = _read_state_file()
    pid = int(data.get("pid") or 0)
    running = False
    lock = _lock_state()
    lock_pid = int(lock.get("pid") or 0)
    if lock_pid and is_pid_running(lock_pid):
        data = {**data, **lock}
        pid = lock_pid
        running = True
    elif pid and data.get("status") == "starting" and is_pid_running(pid):
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
    })
    return data


def _write_run_lock(owner_token: str) -> None:
    _write_json(_run_lock_path(), _run_lock_payload(owner_token))


def _run_lock_payload(owner_token: str) -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "owner_token": owner_token,
        "locked_at": time.time(),
        "heartbeat_at": time.time(),
    }


@contextmanager
def acquire_run_lock(owner_token: str) -> Iterator[bool]:
    path = _run_lock_path()
    fd: int | None = None
    acquired = False
    try:
        while True:
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                payload = json.dumps(_run_lock_payload(owner_token), ensure_ascii=False).encode("utf-8")
                os.write(fd, payload)
                os.close(fd)
                fd = None
                acquired = True
                yield True
                return
            except FileExistsError:
                lock = _lock_state()
                pid = int(lock.get("pid") or 0)
                if pid and is_pid_running(pid):
                    yield False
                    return
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    yield False
                    return
    finally:
        if fd is not None:
            os.close(fd)
        if acquired:
            lock = _lock_state()
            if lock.get("owner_token") == owner_token:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def record_worker_start(owner_token: str, *, mode: str,
                        idle_exit_sec: float | None = None,
                        heartbeat_sec: float | None = None) -> None:
    now = time.time()
    _write_state({
        "pid": os.getpid(),
        "owner_token": owner_token,
        "status": "running",
        "mode": mode,
        "started_at": now,
        "heartbeat_at": now,
        "idle_exit_sec": idle_exit_sec,
        "heartbeat_sec": heartbeat_sec,
    })


def record_spawned(pid: int, owner_token: str, *, cwd: str | None,
                   idle_exit_sec: float, heartbeat_sec: float) -> None:
    now = time.time()
    _write_state({
        "pid": pid,
        "owner_token": owner_token,
        "status": "starting",
        "mode": "short",
        "started_at": now,
        "heartbeat_at": now,
        "idle_exit_sec": idle_exit_sec,
        "heartbeat_sec": heartbeat_sec,
        "cwd": cwd,
    })


def record_heartbeat(owner_token: str) -> None:
    now = time.time()
    _patch_state(owner_token, pid=os.getpid(), status="running", heartbeat_at=now)
    lock = _lock_state()
    if lock.get("owner_token") == owner_token:
        lock["heartbeat_at"] = now
        _write_json(_run_lock_path(), lock)


def record_job_event(owner_token: str, job: Any, status: str) -> None:
    _patch_state(
        owner_token,
        pid=os.getpid(),
        status="running",
        heartbeat_at=time.time(),
        last_job=f"{getattr(job, 'project_id', '?')}__{getattr(job, 'kind', '?')}",
        last_job_status=status,
        last_job_at=time.time(),
    )


def record_worker_exit(owner_token: str, reason: str, note: str = "") -> None:
    _patch_state(
        owner_token,
        pid=os.getpid(),
        status="stopped",
        heartbeat_at=time.time(),
        exited_at=time.time(),
        exit_reason=reason,
        exit_note=note,
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
    path = _start_lock_path()
    fd: int | None = None
    try:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = 0
            if age <= _START_LOCK_STALE_SEC:
                yield False
                return
            try:
                path.unlink()
            except OSError:
                yield False
                return
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"pid={os.getpid()} time={time.time()}\n".encode("ascii"))
        yield True
    finally:
        if fd is not None:
            os.close(fd)
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _spawn_worker_process(cmd: list[str], cwd: str | None, log_path: Path,
                          env: dict[str, str] | None = None) -> int:
    from codev_platform.mcp_runtime import spawn_detached
    return spawn_detached(cmd, cwd, log_path, env=env)


def ensure_worker_running(cfg: dict | None = None, *, cwd: str | Path | None = None,
                          idle_exit_sec: float | None = None,
                          heartbeat_sec: float | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    settings = worker_settings(cfg)
    idle = float(idle_exit_sec or settings.idle_exit_sec)
    heartbeat = float(heartbeat_sec or settings.heartbeat_sec)

    current = worker_status()
    if current.get("running") or _lock_running():
        return {"action": "already-running", "pid": current.get("pid")}

    with _start_lock() as acquired:
        if not acquired:
            return {"action": "already-starting"}
        current = worker_status()
        if current.get("running") or _lock_running():
            return {"action": "already-running", "pid": current.get("pid")}
        from codev_platform.mcp_serve import _venv_python
        py = _venv_python(cfg)
        if not py.exists():
            return {"action": "fail", "error": f"python not found: {py}"}
        owner_token = new_owner_token()
        cmd = [
            str(py), "-m", "codev_platform.cli", "reindex-queue", "worker",
            "--idle-exit-sec", str(int(idle)),
            "--heartbeat-sec", str(int(heartbeat)),
            "--owner-token", owner_token,
        ]
        cwd_str = str(cwd) if cwd is not None else None
        pid = _spawn_worker_process(cmd, cwd_str, logs_dir() / "worker.spawn.log")
        record_spawned(pid, owner_token, cwd=cwd_str, idle_exit_sec=idle, heartbeat_sec=heartbeat)
        return {"action": "spawned", "pid": pid}
