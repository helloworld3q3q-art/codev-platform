"""reindex worker 启动配置与生命周期编排。"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codev_platform.core.config import get as _cfg_get, load_config
from codev_platform.core.paths import logs_dir
from codev_platform.reindex.execution_mode import execution_mode
from codev_platform.reindex.maintenance_gate import (
    _worker_start_permit,
    maintenance_gate_active,
)
from codev_platform.reindex.owner_readiness import OwnerReadiness, inspect_owner_readiness
from codev_platform.reindex.queue import FileSpoolQueue, JobQueue
from codev_platform.reindex.queue_backend_binding import queue_backend_binding

_DEFAULT_IDLE_EXIT_SEC = 600.0
_DEFAULT_HEARTBEAT_SEC = 10.0
_START_LOCK_FILE = "reindex-worker-start.lock"


@dataclass(frozen=True)
class WorkerSettings:
    """短驻 worker 的启动配置。"""

    auto_start: bool
    auto_start_pg: bool
    idle_exit_sec: float
    heartbeat_sec: float


@dataclass(frozen=True)
class WorkerLauncherPorts:
    """launcher 访问 supervisor 状态所需的最小端口集合。"""

    worker_status: Callable[[], dict[str, Any]]
    run_lock_running: Callable[[], bool]
    start_lock: Callable[[], AbstractContextManager[bool]]
    spawn_process: Callable[..., int]
    new_owner_token: Callable[[], str]
    record_spawned: Callable[..., None]


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
    """读取并规范化 worker 启动配置。"""

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
    """判断当前队列后端是否允许自动启动短驻 worker。"""

    with _worker_start_permit() as permitted:
        if not permitted or maintenance_gate_active():
            return False
    settings = worker_settings(cfg)
    if not settings.auto_start:
        return False
    if isinstance(queue, FileSpoolQueue):
        return True
    return settings.auto_start_pg


def start_lock_path(runtime_path: Path) -> Path:
    """返回 worker 启动锁的固定路径。"""

    return runtime_path / _START_LOCK_FILE


@contextmanager
def acquire_start_lock(path: Path, *, stale_sec: float = 60.0) -> Iterator[bool]:
    """独占启动锁；仅回收超过期限的遗留锁文件。"""

    fd: int | None = None
    try:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except OSError:
                age = 0
            if age <= stale_sec:
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


def spawn_worker_process(
    cmd: list[str],
    cwd: str | None,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    """经统一 detached 入口启动 worker，避免继承调用方管道。"""

    from codev_platform.mcp_runtime import spawn_detached

    return spawn_detached(cmd, cwd, log_path, env=env)


def _running_result(ports: WorkerLauncherPorts) -> dict[str, Any] | None:
    current = ports.worker_status()
    if current.get("running") or ports.run_lock_running():
        return {"action": "already-running", "pid": current.get("pid")}
    return None


def _owner_gate_action(queue: object, cfg: dict) -> str | None:
    """根据只读 owner 就绪探针决定是否允许自动拉起。"""

    try:
        binding = queue_backend_binding(queue, cfg)
        readiness = inspect_owner_readiness(binding).status
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return "owner-unavailable"
    if readiness is OwnerReadiness.READY:
        return None
    return {
        OwnerReadiness.BOOTSTRAP_REQUIRED: "bootstrap-required",
        OwnerReadiness.RECOVERY_REQUIRED: "owner-recovery-required",
        OwnerReadiness.BINDING_MISMATCH: "owner-binding-mismatch",
        OwnerReadiness.UNAVAILABLE: "owner-unavailable",
    }.get(readiness, "owner-unavailable")


def _worker_command(
    python: Path,
    *,
    idle_exit_sec: float,
    heartbeat_sec: float,
    owner_token: str,
) -> list[str]:
    return [
        str(python), "-I", "-m", "codev_platform.cli", "reindex-queue", "worker",
        "--idle-exit-sec", str(int(idle_exit_sec)),
        "--heartbeat-sec", str(int(heartbeat_sec)),
        "--owner-token", owner_token,
        "--require-execution-mode", "isolated",
    ]


def ensure_worker_running(
    cfg: dict | None = None,
    *,
    ports: WorkerLauncherPorts,
    queue: object | None = None,
    cwd: str | Path | None = None,
    idle_exit_sec: float | None = None,
    heartbeat_sec: float | None = None,
) -> dict[str, Any]:
    """在启动锁保护下确保唯一短驻 worker 已运行。"""

    with _worker_start_permit() as permitted:
        if not permitted:
            return {"action": "maintenance-gated"}
        cfg = cfg if cfg is not None else load_config()
        mode = execution_mode(cfg)
        if mode != "isolated":
            return {
                "action": "fail",
                "error": "detached worker 仅支持 execution_mode=isolated",
            }
        settings = worker_settings(cfg)
        idle = float(idle_exit_sec or settings.idle_exit_sec)
        heartbeat = float(heartbeat_sec or settings.heartbeat_sec)
        running = _running_result(ports)
        if running is not None:
            return running
        with ports.start_lock() as acquired:
            if not acquired:
                return {"action": "already-starting"}
            running = _running_result(ports)
            if running is not None:
                return running
            gate_action = _owner_gate_action(queue, cfg) if queue is not None else None
            if gate_action is not None:
                return {"action": gate_action}

            from codev_platform.mcp_serve import _platform_runtime_python

            python = _platform_runtime_python()
            if not python.exists():
                return {"action": "fail", "error": f"python not found: {python}"}
            owner_token = ports.new_owner_token()
            command = _worker_command(
                python,
                idle_exit_sec=idle,
                heartbeat_sec=heartbeat,
                owner_token=owner_token,
            )
            cwd_value = str(cwd) if cwd is not None else None
            pid = ports.spawn_process(command, cwd_value, logs_dir() / "worker.spawn.log")
            ports.record_spawned(
                pid,
                owner_token,
                cwd=cwd_value,
                idle_exit_sec=idle,
                heartbeat_sec=heartbeat,
                cmd=command,
                execution_mode="isolated",
            )
            return {"action": "spawned", "pid": pid}
