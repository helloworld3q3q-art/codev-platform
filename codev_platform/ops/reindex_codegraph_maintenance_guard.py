"""CodeGraph 跨版本永久维护条件 guard 的受信安装与证明。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from codev_platform.core.systemd_maintenance_contract import (
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
    CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
)
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    read_optional_root_owned_regular_file_snapshot,
    write_root_owned_regular_file_atomic,
)
from codev_platform.ops.systemd_condition_syntax import (
    SystemdConditionSyntaxError,
    UnknownSystemdConditionDirectiveError,
    verify_no_unknown_condition_directives,
)
from codev_platform.runtime_systemd_gate_contract import (
    DEPLOYMENT_GUARD_DROP_IN_CONTENT,
    deployment_guard_drop_in_path,
)


_CODEGRAPH_UNIT = "codev-mcp-codegraph.service"
_GUARD_MODE = 0o644
_MAX_DROPIN_BYTES = 128 * 1024
_SYSTEMCTL_TIMEOUT_SEC = 10.0

CommandRunner = Callable[..., object]
SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]
FileWriter = Callable[..., None]


class CodegraphMaintenanceGuardError(RuntimeError):
    """CodeGraph 永久维护条件无法安全安装或证明。"""


def ensure_codegraph_maintenance_guard(
    *,
    platform_name: str | None = None,
    writer: FileWriter = write_root_owned_regular_file_atomic,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    command_runner: CommandRunner | None = None,
) -> None:
    """先耐久写入固定 guard，reload 后再证明全部有效 drop-in。"""
    run = _require_linux_adapters(platform_name, command_runner, writer, reader)
    try:
        writer(
            CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
            CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT,
            mode=_GUARD_MODE,
            uid=0,
            gid=0,
        )
        _verify_guard_snapshot(reader)
        _run_systemctl(run, ("systemctl", "daemon-reload"))
        _verify_effective_dropins(run, reader)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except CodegraphMaintenanceGuardError:
        raise
    except Exception as error:
        raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件无法安装") from error


def verify_codegraph_maintenance_guard(
    *,
    platform_name: str | None = None,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    command_runner: CommandRunner | None = None,
) -> None:
    """证明固定原像已被 systemd 最后加载，且不存在其他条件重置。"""
    run = _require_linux_adapters(platform_name, command_runner, None, reader)
    _verify_guard_snapshot(reader)
    _verify_effective_dropins(run, reader)


def _verify_guard_snapshot(reader: SnapshotReader) -> None:
    try:
        snapshot = reader(
            CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH,
            max_bytes=_MAX_DROPIN_BYTES,
        )
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件不受信任") from error
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.content != CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
        or snapshot.mode != _GUARD_MODE
        or snapshot.uid != 0
        or snapshot.gid != 0
    ):
        raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件不受信任")


def _verify_effective_dropins(run: CommandRunner, reader: SnapshotReader) -> None:
    paths = _read_effective_dropin_paths(run)
    guard = PurePosixPath(CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH.as_posix())
    deployment = deployment_guard_drop_in_path(_CODEGRAPH_UNIT)
    if not paths or paths[-1] != guard:
        if guard not in paths:
            raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件未进入有效解析")
        raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件不是最后有效项")
    if len(paths) != len(set(paths)):
        raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 路径重复")
    for path in paths:
        snapshot = _read_dropin_snapshot(reader, path)
        if path == guard:
            if (
                snapshot.content != CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT
                or snapshot.mode != _GUARD_MODE
                or snapshot.gid != 0
            ):
                raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件不受信任")
            continue
        if path == deployment:
            if (
                snapshot.content != DEPLOYMENT_GUARD_DROP_IN_CONTENT
                or snapshot.mode != _GUARD_MODE
                or snapshot.gid != 0
            ):
                raise CodegraphMaintenanceGuardError("CodeGraph 部署总门禁条件不受信任")
            continue
        _reject_condition_directives(snapshot.content)


def _read_dropin_snapshot(
    reader: SnapshotReader,
    path: PurePosixPath,
) -> RootOwnedRegularFileSnapshot:
    try:
        snapshot = reader(Path(path.as_posix()), max_bytes=_MAX_DROPIN_BYTES)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception as error:
        raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 不受信任") from error
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.uid != 0
        or snapshot.mode & 0o022
    ):
        raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 不受信任")
    return snapshot


def _reject_condition_directives(content: bytes) -> None:
    try:
        verify_no_unknown_condition_directives(content)
    except UnknownSystemdConditionDirectiveError:
        raise CodegraphMaintenanceGuardError("CodeGraph drop-in 含未知条件或重置") from None
    except SystemdConditionSyntaxError:
        raise CodegraphMaintenanceGuardError("CodeGraph drop-in 条件语法无法证明") from None


def _read_effective_dropin_paths(run: CommandRunner) -> tuple[PurePosixPath, ...]:
    result = _run_systemctl(
        run,
        (
            "systemctl",
            "show",
            _CODEGRAPH_UNIT,
            "--property=DropInPaths",
        ),
    )
    output = getattr(result, "stdout", None)
    if type(output) is not str:
        raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 输出无效")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith("DropInPaths="):
        raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 输出无效")
    raw = lines[0].removeprefix("DropInPaths=")
    paths: list[PurePosixPath] = []
    for item in raw.split():
        path = PurePosixPath(item)
        if (
            not path.is_absolute()
            or path.as_posix() != item
            or ".." in path.parts
            or "\\" in item
            or "\x00" in item
        ):
            raise CodegraphMaintenanceGuardError("CodeGraph 有效 drop-in 路径无效")
        paths.append(path)
    return tuple(paths)


def _require_linux_adapters(
    platform_name: str | None,
    command_runner: CommandRunner | None,
    writer: FileWriter | None,
    reader: SnapshotReader,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise CodegraphMaintenanceGuardError("当前平台不支持 CodeGraph 永久维护条件")
    run = _default_command_runner if command_runner is None else command_runner
    items = (run, reader) if writer is None else (run, reader, writer)
    if not all(callable(item) for item in items):
        raise CodegraphMaintenanceGuardError("CodeGraph 永久维护条件适配器不可用")
    return run


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> object:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except MemoryError:
        raise
    except Exception as error:
        raise CodegraphMaintenanceGuardError("CodeGraph systemd 条件命令无法执行") from error
    if getattr(result, "returncode", None) != 0:
        raise CodegraphMaintenanceGuardError("CodeGraph systemd 条件命令失败")
    return result


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
    "CODEGRAPH_MAINTENANCE_GUARD_DROPIN_CONTENT",
    "CODEGRAPH_MAINTENANCE_GUARD_DROPIN_PATH",
    "CodegraphMaintenanceGuardError",
    "ensure_codegraph_maintenance_guard",
    "verify_codegraph_maintenance_guard",
]
