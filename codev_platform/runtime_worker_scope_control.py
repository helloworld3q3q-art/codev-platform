"""root-fd worker user systemd 控制面与 MainPID 收口原语。"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
from pathlib import Path

from codev_platform.runtime_execution_trust import (
    RuntimeExecutionTrustError,
    verify_root_controlled_executable,
)
from codev_platform.runtime_process import isolated_process_environment
from codev_platform.runtime_worker_pidfd import (
    RuntimeWorkerPidfdError,
    terminate_pidfd,
)


SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
ENV = "/usr/bin/env"
SCOPE_PROPERTY_NAMES = (
    "ActiveState",
    "MainPID",
    "KillMode",
    "ExitType",
    "KillSignal",
    "SendSIGKILL",
    "Restart",
    "RemainAfterExit",
)
_SIGKILL = int(getattr(signal, "SIGKILL", 9))


class RuntimeWorkerScopeControlError(RuntimeError):
    """root-fd worker user systemd 控制面无法安全证明或调用。"""


def require_systemd_client_binaries(*paths: str) -> None:
    """仅在父端命名空间验证控制二进制所有权，避开 sandbox uid 映射。"""
    try:
        for path in paths:
            verify_root_controlled_executable(Path(path))
    except RuntimeExecutionTrustError as error:
        raise RuntimeWorkerScopeControlError("root-fd worker systemd 客户端不受信任") from error


def user_systemd_client_environment() -> dict[str, str]:
    runtime_directory = _require_user_runtime_directory()
    return isolated_process_environment(
        overrides={
            "XDG_RUNTIME_DIR": str(runtime_directory),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime_directory / 'bus'}",
        }
    )


def verify_scope_control(unit: str, main_process_id: int) -> None:
    """证明当前 bootstrap 是可由 manager 严格收口的 MainPID。"""
    if type(main_process_id) is not int or main_process_id <= 0:
        raise RuntimeWorkerScopeControlError("root-fd worker MainPID 无效")
    arguments = ("show", unit, *tuple(f"--property={name}" for name in SCOPE_PROPERTY_NAMES))
    completed = _run_systemctl(arguments, timeout=1.0)
    expected = {
        "ActiveState": "active",
        "MainPID": str(main_process_id),
        "KillMode": "control-group",
        "ExitType": "main",
        "KillSignal": str(_SIGKILL),
        "SendSIGKILL": "yes",
        "Restart": "no",
        "RemainAfterExit": "no",
    }
    if (
        completed is None
        or completed.returncode != 0
        or _parse_scope_properties(completed.stdout) != expected
    ):
        raise RuntimeWorkerScopeControlError("root-fd worker systemd 控制面不可用")


def kill_scope(unit: str) -> bool:
    """请求 manager 用 control-group 强杀 unit；调用者可能同时被回收。"""
    completed = _run_systemctl(
        ("kill", "--kill-whom=all", "--signal=SIGKILL", unit),
        timeout=1.0,
    )
    return completed is not None and completed.returncode == 0


def terminate_scope_main(pidfd: int) -> bool:
    """按精确 pidfd 中止 bootstrap MainPID，触发 ExitType=main 收口。"""
    try:
        return terminate_pidfd(pidfd)
    except RuntimeWorkerPidfdError:
        return False


def _run_systemctl(
    arguments: tuple[str, ...],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(
            (SYSTEMCTL, "--user", *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
            env=user_systemd_client_environment(),
        )
    except (OSError, subprocess.SubprocessError, RuntimeWorkerScopeControlError):
        return None


def _parse_scope_properties(raw: bytes) -> dict[str, str] | None:
    if type(raw) is not bytes:
        return None
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    properties: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition("=")
        if not separator or name not in SCOPE_PROPERTY_NAMES or name in properties or not value:
            return None
        properties[name] = value
    return properties if set(properties) == set(SCOPE_PROPERTY_NAMES) else None


def _require_user_runtime_directory() -> Path:
    if not hasattr(os, "geteuid"):
        raise RuntimeWorkerScopeControlError("root-fd worker 缺少 Linux 用户身份")
    runtime_directory = Path("/run/user") / str(os.geteuid())
    bus = runtime_directory / "bus"
    try:
        metadata = runtime_directory.stat()
        bus_metadata = bus.stat()
    except OSError as error:
        raise RuntimeWorkerScopeControlError("root-fd worker user systemd bus 不可用") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & (stat.S_IWGRP | stat.S_IWOTH)
        or not stat.S_ISSOCK(bus_metadata.st_mode)
        or bus_metadata.st_uid != os.geteuid()
    ):
        raise RuntimeWorkerScopeControlError("root-fd worker user systemd bus 身份无效")
    return runtime_directory


__all__ = [
    "ENV",
    "SYSTEMCTL",
    "SYSTEMD_RUN",
    "RuntimeWorkerScopeControlError",
    "kill_scope",
    "require_systemd_client_binaries",
    "terminate_scope_main",
    "user_systemd_client_environment",
    "verify_scope_control",
]
