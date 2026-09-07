"""固定 systemd Condition drop-in 的共存与最终有效配置证明。"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from codev_platform.mcp_systemd_unit_registry import MANAGED_SYSTEMD_UNIT_NAMES
from codev_platform.ops.reindex_codegraph_resume_managed_path import (
    RootOwnedRegularFileSnapshot,
    read_optional_root_owned_regular_file_snapshot,
)
from codev_platform.ops.systemd_condition_syntax import (
    SystemdConditionSyntaxError,
    UnknownSystemdConditionDirectiveError,
    verify_no_unknown_condition_directives,
)


_MAX_DROP_IN_BYTES = 128 * 1024
_SYSTEMCTL_TIMEOUT_SEC = 10.0

CommandRunner = Callable[..., object]
SnapshotReader = Callable[..., RootOwnedRegularFileSnapshot | None]


class SystemdConditionGuardError(RuntimeError):
    """固定条件未被 systemd 安全加载，或存在可重置它的未知条件。"""


@dataclass(frozen=True, slots=True)
class SystemdConditionSpec:
    """一个允许进入最终有效配置的固定条件原像。"""

    path: Path
    content: bytes

    def __post_init__(self) -> None:
        try:
            path = Path(self.path)
            posix = PurePosixPath(path.as_posix())
        except (TypeError, ValueError):
            raise SystemdConditionGuardError("systemd 固定条件规格无效") from None
        if (
            not posix.is_absolute()
            or path.suffix != ".conf"
            or ".." in posix.parts
            or type(self.content) is not bytes
            or not self.content
            or len(self.content) > _MAX_DROP_IN_BYTES
        ):
            raise SystemdConditionGuardError("systemd 固定条件规格无效")


def verify_systemd_condition_guard(
    unit: str,
    *,
    required: SystemdConditionSpec,
    allowed: tuple[SystemdConditionSpec, ...] = (),
    platform_name: str | None = None,
    reader: SnapshotReader = read_optional_root_owned_regular_file_snapshot,
    command_runner: CommandRunner | None = None,
) -> None:
    """证明 required 已生效；已知条件必须精确，未知 drop-in 不得含重置或新条件。"""
    run = _require_adapters(unit, required, allowed, platform_name, reader, command_runner)
    paths = _read_effective_paths(run, unit)
    required_path = PurePosixPath(required.path.as_posix())
    if required_path not in paths:
        raise SystemdConditionGuardError("systemd 固定条件未进入有效解析")
    if len(paths) != len(set(paths)):
        raise SystemdConditionGuardError("systemd 有效 drop-in 路径重复")
    known = {required_path: required}
    known.update((PurePosixPath(item.path.as_posix()), item) for item in allowed)
    for path in paths:
        snapshot = _read_snapshot(reader, path)
        spec = known.get(path)
        if spec is None:
            _reject_unknown_condition(snapshot.content)
            continue
        if (
            snapshot.content != spec.content
            or snapshot.mode != 0o644
            or snapshot.uid != 0
            or snapshot.gid != 0
        ):
            raise SystemdConditionGuardError("systemd 固定条件不受信任")


def _require_adapters(
    unit: str,
    required: SystemdConditionSpec,
    allowed: tuple[SystemdConditionSpec, ...],
    platform_name: str | None,
    reader: SnapshotReader,
    command_runner: CommandRunner | None,
) -> CommandRunner:
    platform = sys.platform if platform_name is None else platform_name
    if type(platform) is not str or not platform.startswith("linux"):
        raise SystemdConditionGuardError("当前平台不支持 systemd 固定条件证明")
    if type(unit) is not str or unit not in MANAGED_SYSTEMD_UNIT_NAMES:
        raise SystemdConditionGuardError("systemd 固定条件 unit 不受管")
    if type(required) is not SystemdConditionSpec or type(allowed) is not tuple:
        raise SystemdConditionGuardError("systemd 固定条件规格无效")
    if any(type(item) is not SystemdConditionSpec for item in allowed):
        raise SystemdConditionGuardError("systemd 固定条件规格无效")
    paths = (required.path, *(item.path for item in allowed))
    if len(paths) != len(set(paths)):
        raise SystemdConditionGuardError("systemd 固定条件路径重复")
    run = _default_command_runner if command_runner is None else command_runner
    if not callable(run) or not callable(reader):
        raise SystemdConditionGuardError("systemd 固定条件适配器不可用")
    return run


def _read_effective_paths(run: CommandRunner, unit: str) -> tuple[PurePosixPath, ...]:
    result = _run_systemctl(
        run,
        ("/usr/bin/systemctl", "show", unit, "--property=DropInPaths"),
    )
    output = getattr(result, "stdout", None)
    if type(output) is not str:
        raise SystemdConditionGuardError("systemd 有效 drop-in 输出无效")
    lines = output.splitlines()
    if len(lines) != 1 or not lines[0].startswith("DropInPaths="):
        raise SystemdConditionGuardError("systemd 有效 drop-in 输出无效")
    paths: list[PurePosixPath] = []
    for item in lines[0].removeprefix("DropInPaths=").split():
        path = PurePosixPath(item)
        if (
            not path.is_absolute()
            or path.as_posix() != item
            or ".." in path.parts
            or "\\" in item
            or "\x00" in item
        ):
            raise SystemdConditionGuardError("systemd 有效 drop-in 路径无效")
        paths.append(path)
    return tuple(paths)


def _read_snapshot(
    reader: SnapshotReader,
    path: PurePosixPath,
) -> RootOwnedRegularFileSnapshot:
    try:
        snapshot = reader(Path(path.as_posix()), max_bytes=_MAX_DROP_IN_BYTES)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise SystemdConditionGuardError("systemd 有效 drop-in 不受信任") from None
    if (
        type(snapshot) is not RootOwnedRegularFileSnapshot
        or snapshot.uid != 0
        or snapshot.mode & 0o022
    ):
        raise SystemdConditionGuardError("systemd 有效 drop-in 不受信任")
    return snapshot


def _reject_unknown_condition(content: bytes) -> None:
    try:
        verify_no_unknown_condition_directives(content)
    except UnknownSystemdConditionDirectiveError:
        raise SystemdConditionGuardError("systemd drop-in 含未知条件或重置") from None
    except SystemdConditionSyntaxError:
        raise SystemdConditionGuardError("systemd 未知条件语法无法证明") from None


def _run_systemctl(run: CommandRunner, command: tuple[str, ...]) -> object:
    try:
        result = run(command, timeout_sec=_SYSTEMCTL_TIMEOUT_SEC)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        raise SystemdConditionGuardError("systemd 固定条件命令无法执行") from None
    if getattr(result, "returncode", None) != 0:
        raise SystemdConditionGuardError("systemd 固定条件命令失败")
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
    "SystemdConditionGuardError",
    "SystemdConditionSpec",
    "verify_systemd_condition_guard",
]
