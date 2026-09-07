"""Webhook 维护条件 guard 的安装与有效解析证明。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from codev_platform.core.systemd_maintenance_contract import (
    WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT,
    WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    read_optional_root_owned_regular_file_snapshot,
    write_root_owned_regular_file_atomic,
)
from codev_platform.ops.systemd_condition_guard import (
    SystemdConditionGuardError,
    SystemdConditionSpec,
    verify_systemd_condition_guard,
)
from codev_platform.runtime_ingress_gate import (
    INGRESS_GATE_DROP_IN_CONTENT,
    ingress_gate_drop_in_path,
)
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    deployment_guard_drop_in_path,
)


_WEBHOOK_UNIT = "codev-webhook.service"
_GUARD_MODE = 0o644
_MAX_DROPIN_BYTES = 128 * 1024
_SYSTEMCTL_TIMEOUT_SEC = 10.0

CommandRunner = Callable[..., object]
SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[..., None]


class WebhookMaintenanceGuardError(RuntimeError):
    """Webhook 维护条件无法安全安装或证明。"""


def ensure_webhook_maintenance_guard(
    *,
    platform_name: str | None = None,
    writer: FileWriter = write_root_owned_regular_file_atomic,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    command_runner: CommandRunner | None = None,
) -> None:
    """先持久化条件 guard，reload 后复证 systemd 已按固定条件解析。"""
    run = _require_linux_adapters(platform_name, command_runner, writer, reader)
    try:
        writer(
            WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH,
            WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT,
            mode=_GUARD_MODE,
            uid=0,
            gid=0,
        )
        _verify_guard_snapshot(reader)
        _run_systemctl(run, ("systemctl", "daemon-reload"))
        _verify_effective_guard(run, reader, platform_name=platform_name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except WebhookMaintenanceGuardError:
        raise
    except Exception as error:
        raise WebhookMaintenanceGuardError("Webhook 永久维护条件无法安装") from error


def verify_webhook_maintenance_guard(
    *,
    platform_name: str | None = None,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    command_runner: CommandRunner | None = None,
) -> None:
    """证明 guard 原像和所有已生效条件均未被未知 drop-in 重置。"""
    run = _require_linux_adapters(platform_name, command_runner, None, reader)
    try:
        _verify_guard_snapshot(reader)
        _verify_effective_guard(run, reader, platform_name=platform_name)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except WebhookMaintenanceGuardError:
        raise
    except Exception as error:
        raise WebhookMaintenanceGuardError("Webhook 永久维护条件无法证明") from error


def _verify_guard_snapshot(reader: SnapshotReader) -> None:
    snapshot = reader(WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH, max_bytes=_MAX_DROPIN_BYTES)
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.content != WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT
        or snapshot.mode != _GUARD_MODE
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise WebhookMaintenanceGuardError("Webhook 永久维护条件不受信任")


def _verify_effective_guard(
    run: CommandRunner,
    reader: SnapshotReader,
    *,
    platform_name: str | None,
) -> None:
    try:
        verify_systemd_condition_guard(
            _WEBHOOK_UNIT,
            required=SystemdConditionSpec(
                WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH,
                WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT,
            ),
            allowed=(
                SystemdConditionSpec(
                    Path(deployment_guard_drop_in_path(_WEBHOOK_UNIT).as_posix()),
                    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
                ),
                SystemdConditionSpec(
                    Path(ingress_gate_drop_in_path().as_posix()),
                    INGRESS_GATE_DROP_IN_CONTENT,
                ),
            ),
            platform_name=platform_name,
            reader=reader,
            command_runner=run,
        )
    except SystemdConditionGuardError as error:
        raise WebhookMaintenanceGuardError("Webhook 永久维护条件未进入有效解析") from error


def _require_linux_adapters(
    platform_name: str | None,
    command_runner: CommandRunner | None,
    writer: FileWriter | None,
    reader: SnapshotReader,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise WebhookMaintenanceGuardError("当前平台不支持 Webhook 永久维护条件")
    run = _default_command_runner if command_runner is None else command_runner
    adapters = (run, reader) if writer is None else (run, writer, reader)
    if not all(callable(item) for item in adapters):
        raise WebhookMaintenanceGuardError("Webhook 永久维护条件适配器不可用")
    return run


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> None:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise WebhookMaintenanceGuardError("Webhook systemd 条件命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise WebhookMaintenanceGuardError("Webhook systemd 条件命令失败")


def _default_command_runner(command: tuple[str, ...], *, timeout_sec: float) -> object:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )


__all__ = [
    "WEBHOOK_MAINTENANCE_GUARD_DROPIN_CONTENT",
    "WEBHOOK_MAINTENANCE_GUARD_DROPIN_PATH",
    "WebhookMaintenanceGuardError",
    "ensure_webhook_maintenance_guard",
    "verify_webhook_maintenance_guard",
]
