"""Runtime helpers for starting MCP endpoint processes.

`mcp_serve` owns endpoint discovery and probing. This module owns only the
process-spawn side effect so other write-side jobs can ensure a single endpoint
without duplicating detached-process details.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol


class SpawnableEndpoint(Protocol):
    name: str
    kind: str
    port: int
    cmd: list[str] | None
    cwd: str | None


def default_log_dir() -> Path:
    path = Path(__file__).resolve().parent / "mcp_serve_logs"
    path.mkdir(exist_ok=True)
    return path


def spawn_env_for_endpoint(ep: SpawnableEndpoint) -> dict[str, str] | None:
    if ep.kind != "chroma":
        return None
    return {
        **os.environ,
        "PLATFORM_DOCS_DAEMON_PORT": str(ep.port),
        "PLATFORM_DOCS_PREWARM": "true",
    }


def spawn_detached(
    cmd: list[str],
    cwd: str | None,
    log_path: Path,
    env: dict[str, str] | None = None,
) -> int:
    if sys.platform == "win32":
        creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    else:
        creationflags = 0
    spawn_env = env if env is not None else os.environ.copy()
    log_handle = open(log_path, "ab", buffering=0)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
            close_fds=False,
            env=spawn_env,
            start_new_session=(sys.platform != "win32"),
        )
        return proc.pid
    finally:
        log_handle.close()


def spawn_endpoint(ep: SpawnableEndpoint, *, log_dir: Path | None = None) -> dict[str, Any]:
    if ep.cmd is None:
        return {
            "name": ep.name,
            "action": "skip",
            "status": "down",
            "note": "无 spawn 命令(外部托管)",
        }
    exe = Path(ep.cmd[0])
    if not exe.exists():
        return {
            "name": ep.name,
            "action": "fail",
            "status": "down",
            "error": f"可执行不存在: {exe}",
        }
    out_dir = log_dir or default_log_dir()
    out_dir.mkdir(exist_ok=True)
    pid = spawn_detached(
        ep.cmd,
        ep.cwd,
        out_dir / f"{ep.name.replace(':', '_')}.log",
        env=spawn_env_for_endpoint(ep),
    )
    note = "chroma daemon 预热模型 ~30-60s" if ep.kind == "chroma" else ""
    return {
        "name": ep.name,
        "action": "spawned",
        "status": "starting",
        "pid": pid,
        "note": note,
    }
