"""启动 MCP 端点进程的运行时辅助。

`mcp_serve` 负责端点发现与探测；本模块只负责进程启动副作用，让其他写侧任务能够
确保单个端点已启动，而无需复制后台进程细节。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

import codev_platform.core.runtime_artifacts as runtime_artifacts
from codev_platform.core.runtime_artifact_io import (
    open_runtime_artifact_binary,
    prepare_runtime_artifact_directory,
)
from codev_platform.mcp_managed_service import managed_mcp_service_required
from codev_platform.mcp_systemd_unit_registry import mcp_systemd_unit_for_kind


class SpawnableEndpoint(Protocol):
    name: str
    kind: str
    port: int
    cmd: list[str] | None
    cwd: str | None


def default_log_dir() -> Path:
    return prepare_runtime_artifact_directory(runtime_artifacts.serve_mcp_log_dir())


def spawn_env_for_endpoint(ep: SpawnableEndpoint) -> dict[str, str] | None:
    if ep.kind != "chroma":
        return None
    return {
        **os.environ,
        "PLATFORM_DOCS_DAEMON_PORT": str(ep.port),
        "PLATFORM_DOCS_PREWARM": "true",
    }


def _codegraph_start_permitted() -> bool:
    """仅 CodeGraph 启动路径延迟读取维护门禁；异常一律拒绝。"""
    try:
        from codev_platform.codegraph.maintenance_gate import codegraph_start_permitted

        return codegraph_start_permitted() is True
    except Exception:
        return False


def _codegraph_requires_managed_service() -> bool:
    """Linux systemd 环境只允许固定 CodeGraph unit，异常也不降级为 detached。"""
    try:
        from codev_platform.codegraph.maintenance_gate import codegraph_requires_managed_service

        return codegraph_requires_managed_service() is True
    except Exception:
        return True


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
    log_handle = open_runtime_artifact_binary(log_path)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=log_handle,
            creationflags=creationflags,
            close_fds=True,
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
    if ep.kind == "codegraph" and not _codegraph_start_permitted():
        return {
            "name": ep.name,
            "action": "fail",
            "status": "down",
            "error": "reindex 维护门禁已激活或状态不可证明，拒绝启动 CodeGraph",
        }
    requires_managed_service = managed_mcp_service_required() or (
        ep.kind == "codegraph" and _codegraph_requires_managed_service()
    )
    if requires_managed_service:
        try:
            unit = mcp_systemd_unit_for_kind(ep.kind)
        except ValueError:
            return {
                "name": ep.name,
                "action": "fail",
                "status": "down",
                "error": "MCP 端点没有固定 systemd unit，拒绝脱离式启动",
            }
        return {
            "name": ep.name,
            "action": "skip",
            "status": "down",
            "note": f"当前 Linux systemd 环境只允许受管 {unit}，跳过脱离式 MCP 启动",
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
    out_dir = prepare_runtime_artifact_directory(out_dir)
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
