"""systemd 受管进程的中性契约与生命周期编排。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, fields
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from collections.abc import Callable

from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFilePathError,
    require_systemd_environment_file_path,
)


_ERROR_MESSAGE = "systemd 受管进程执行失败"
_SLOT = re.compile(r"[a-z][a-z0-9-]{0,47}\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_MAX_RUNTIME_SEC = 43_200.0
_MAX_STOP_SEC = 120.0
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_TASKS = 4096
_MAX_MEMORY_BYTES = 64 * 1024**3
_MAX_CPU_QUOTA_PERCENT = 1600


class RuntimeManagedProcessError(RuntimeError):
    """受管进程契约、执行或最终清算无法证明。"""


class RuntimeManagedProcessStateError(RuntimeManagedProcessError):
    """受管 unit 的最终死亡状态无法证明。"""


@dataclass(frozen=True, slots=True)
class ManagedProcessLimits:
    """一次执行不可由运行时配置扩大的固定资源边界。"""

    runtime_sec: float
    stop_sec: float
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    tasks_max: int
    memory_high_bytes: int
    memory_max_bytes: int
    memory_swap_max_bytes: int
    cpu_quota_percent: int

    def __post_init__(self) -> None:
        if not _bounded_positive_number(self.runtime_sec, _MAX_RUNTIME_SEC):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_positive_number(self.stop_sec, _MAX_STOP_SEC):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_nonnegative_int(self.stdout_limit_bytes, _MAX_OUTPUT_BYTES):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_nonnegative_int(self.stderr_limit_bytes, _MAX_OUTPUT_BYTES):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_positive_int(self.tasks_max, _MAX_TASKS):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_positive_int(self.memory_high_bytes, _MAX_MEMORY_BYTES):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_positive_int(self.memory_max_bytes, _MAX_MEMORY_BYTES):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if self.memory_high_bytes > self.memory_max_bytes:
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_nonnegative_int(
            self.memory_swap_max_bytes,
            _MAX_MEMORY_BYTES,
        ):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if not _bounded_positive_int(
            self.cpu_quota_percent,
            _MAX_CPU_QUOTA_PERCENT,
        ):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)


@dataclass(frozen=True, slots=True)
class ManagedProcessSpec:
    """受管 unit 的稳定语义槽、命令、身份与资源规格。"""

    unit_slot: str
    argv: tuple[str, ...]
    working_directory: str
    environment: tuple[tuple[str, str], ...]
    environment_file: Path | None
    user: str | None
    limits: ManagedProcessLimits

    def __post_init__(self) -> None:
        if type(self.unit_slot) is not str or _SLOT.fullmatch(self.unit_slot) is None:
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        _validate_argv(self.argv)
        _validate_working_directory(self.working_directory)
        _validate_environment(self.environment)
        _validate_environment_file(self.environment_file)
        if self.user is not None and (
            type(self.user) is not str or self.user == "root" or _USER.fullmatch(self.user) is None
        ):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)
        if type(self.limits) is not ManagedProcessLimits:
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)


@dataclass(frozen=True, slots=True)
class ManagedProcessResult:
    """受管 payload 的最小结果，不保留 argv、环境或 unit。"""

    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if (
            type(self.returncode) is not int
            or type(self.stdout) is not bytes
            or type(self.stderr) is not bytes
        ):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)


@dataclass(frozen=True, slots=True)
class ManagedProcessPorts:
    """生命周期编排所需的窄 systemd 与流式输出端口。"""

    lock_unit: Callable[[str, float], AbstractContextManager[None]]
    settle_unit: Callable[[str, float], None]
    spawn_unit: Callable[[ManagedProcessSpec, str], subprocess.Popen[bytes]]
    collect_output: Callable[
        [subprocess.Popen[bytes], int, int, float],
        ManagedProcessResult,
    ]
    abort_client: Callable[[subprocess.Popen[bytes], float], None]

    def __post_init__(self) -> None:
        if any(not callable(getattr(self, item.name)) for item in fields(self)):
            raise RuntimeManagedProcessError(_ERROR_MESSAGE)


def managed_unit_name(unit_slot: str) -> str:
    """从稳定语义槽生成唯一、可在下次调用恢复的 unit 名。"""
    if type(unit_slot) is not str or _SLOT.fullmatch(unit_slot) is None:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    return f"codev-runtime-{unit_slot}.service"


def run_managed_process(
    spec: ManagedProcessSpec,
    *,
    ports: ManagedProcessPorts | None = None,
    platform_name: str | None = None,
) -> ManagedProcessResult:
    """在同一 slot 锁内执行“残留收敛→运行→死亡证明”。"""
    _require_linux_root(platform_name, require_root=ports is None)
    if type(spec) is not ManagedProcessSpec:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    active = default_ports() if ports is None else ports
    if type(active) is not ManagedProcessPorts:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    unit = managed_unit_name(spec.unit_slot)
    result: ManagedProcessResult | None = None
    failed = False
    state_unproven = False
    try:
        with active.lock_unit(unit, float(spec.limits.stop_sec)):
            process: subprocess.Popen[bytes] | None = None
            try:
                active.settle_unit(unit, float(spec.limits.stop_sec))
                process = active.spawn_unit(spec, unit)
                result = active.collect_output(
                    process,
                    spec.limits.stdout_limit_bytes,
                    spec.limits.stderr_limit_bytes,
                    time.monotonic() + float(spec.limits.runtime_sec),
                )
                active.settle_unit(unit, float(spec.limits.stop_sec))
            except (KeyboardInterrupt, SystemExit, MemoryError):
                try:
                    _cleanup_failed_run(active, unit, process, spec.limits.stop_sec)
                except RuntimeManagedProcessStateError:
                    state_unproven = True
                else:
                    raise
            except Exception:
                try:
                    _cleanup_failed_run(active, unit, process, spec.limits.stop_sec)
                except RuntimeManagedProcessStateError:
                    state_unproven = True
                failed = True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if state_unproven:
        raise RuntimeManagedProcessStateError("systemd 受管进程安全状态未证明") from None
    if failed or result is None:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE) from None
    return result


def default_ports() -> ManagedProcessPorts:
    """延迟组合 systemd 与输出适配器，保持编排层无基础设施细节。"""
    from codev_platform.runtime_process_output import collect_process_output
    from codev_platform.runtime_systemd_process import (
        abort_systemd_run_client,
        acquire_systemd_unit_lock,
        settle_systemd_unit,
        spawn_systemd_unit,
    )

    def collect(
        process: subprocess.Popen[bytes],
        stdout_limit_bytes: int,
        stderr_limit_bytes: int,
        deadline: float,
    ) -> ManagedProcessResult:
        output = collect_process_output(
            process,
            stdout_limit_bytes,
            stderr_limit_bytes,
            deadline,
        )
        return ManagedProcessResult(
            output.returncode,
            output.stdout,
            output.stderr,
        )

    return ManagedProcessPorts(
        lock_unit=acquire_systemd_unit_lock,
        settle_unit=settle_systemd_unit,
        spawn_unit=spawn_systemd_unit,
        collect_output=collect,
        abort_client=abort_systemd_run_client,
    )


def _cleanup_failed_run(
    ports: ManagedProcessPorts,
    unit: str,
    process: subprocess.Popen[bytes] | None,
    timeout_sec: float,
) -> None:
    deadline = time.monotonic() + float(timeout_sec)
    try:
        ports.settle_unit(
            unit,
            min(float(timeout_sec) / 3.0, _remaining_cleanup_time(deadline)),
        )
    except BaseException:
        pass
    if process is not None and _process_alive(process):
        try:
            ports.abort_client(
                process,
                min(float(timeout_sec) / 3.0, _remaining_cleanup_time(deadline)),
            )
        except BaseException:
            pass
    failed = process is not None and _process_alive(process)
    try:
        ports.settle_unit(unit, _remaining_cleanup_time(deadline))
    except BaseException:
        failed = True
    if failed:
        raise RuntimeManagedProcessStateError("systemd 受管进程安全状态未证明")


def _process_alive(process: subprocess.Popen[bytes]) -> bool:
    try:
        return process.poll() is None
    except BaseException:
        return True


def _remaining_cleanup_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeManagedProcessStateError("systemd 受管进程安全状态未证明")
    return remaining


def _validate_argv(argv: object) -> None:
    if type(argv) is not tuple or not argv:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    if any(type(value) is not str or "\x00" in value for value in argv):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    executable = PurePosixPath(argv[0])
    if (
        not executable.is_absolute()
        or not argv[0].startswith("/")
        or argv[0].startswith("//")
        or ".." in executable.parts
        or executable.as_posix() != argv[0]
    ):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)


def _validate_working_directory(value: object) -> None:
    if value == "%h":
        return
    if type(value) is not str or "\x00" in value:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    selected = PurePosixPath(value)
    if (
        not selected.is_absolute()
        or not value.startswith("/")
        or value.startswith("//")
        or ".." in selected.parts
        or selected.as_posix() != value
    ):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)


def _validate_environment(value: object) -> None:
    if value != ():
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)


def _validate_environment_file(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, Path):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    try:
        literal = require_systemd_environment_file_path(value.as_posix())
    except SystemdEnvironmentFilePathError:
        raise RuntimeManagedProcessError(_ERROR_MESSAGE) from None
    selected = PurePosixPath(literal)
    if selected.as_posix().startswith("/mnt/") or selected.as_posix() != value.as_posix():
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)


def _bounded_positive_number(value: object, maximum: float) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and 0 < float(value) <= maximum


def _bounded_positive_int(value: object, maximum: int) -> bool:
    return type(value) is int and 0 < value <= maximum


def _bounded_nonnegative_int(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _require_linux_root(platform_name: str | None, *, require_root: bool) -> None:
    selected = sys.platform if platform_name is None else platform_name
    if type(selected) is not str or not selected.startswith("linux"):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)
    if require_root and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        raise RuntimeManagedProcessError(_ERROR_MESSAGE)


__all__ = [
    "ManagedProcessLimits",
    "ManagedProcessPorts",
    "ManagedProcessResult",
    "ManagedProcessSpec",
    "RuntimeManagedProcessError",
    "RuntimeManagedProcessStateError",
    "default_ports",
    "managed_unit_name",
    "run_managed_process",
]
