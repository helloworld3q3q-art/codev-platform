"""systemd transient service 的固定命令、slot 锁与死亡证明适配器。"""

from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from collections.abc import Iterator

from codev_platform.core.cgroup_events import parse_cgroup_events
from codev_platform.core.process_tree import kill_process_tree, popen_tree
from codev_platform.core.systemd_environment_file import (
    SystemdEnvironmentFileError,
    SystemdEnvironmentFilePathError,
    require_systemd_environment_file_path,
    validate_systemd_environment_file,
)
from codev_platform.runtime_execution_trust import verify_root_controlled_executable
from codev_platform.runtime_managed_process import ManagedProcessSpec


_ERROR_MESSAGE = "systemd 受管进程适配器失败"
_UNIT = re.compile(r"codev-runtime-[a-z][a-z0-9-]{0,47}\.service\Z")
_CGROUP_PART = re.compile(r"[A-Za-z0-9_.@:-]+\Z")
_SYSTEMD_RUN = Path("/usr/bin/systemd-run")
_SYSTEMCTL = Path("/usr/bin/systemctl")
_WRAPPER_PYTHON = Path("/usr/bin/python3")
_CGROUP_ROOT = Path("/sys/fs/cgroup")
_LOCK_ROOT = Path("/run/lock/codev-platform-runtime")
_MAX_STATE_BYTES = 16 * 1024
_EXEC_EXACT_ENVIRONMENT = r"""
import json
import os
import sys

keys = json.loads(sys.argv[1])
command = sys.argv[2:]
environment = {key: os.environ[key] for key in keys}
os.execve(command[0], command, environment)
"""


class RuntimeSystemdProcessError(RuntimeError):
    """systemd 命令、互斥或 cgroup 死亡证明失败。"""


def build_systemd_run_command(
    spec: ManagedProcessSpec,
    unit: str,
) -> tuple[str, ...]:
    """生成不可由调用方覆盖的 systemd-run 固定参数。"""
    _require_spec_and_unit(spec, unit)
    limits = spec.limits
    command = [
        os.fspath(_SYSTEMD_RUN),
        f"--unit={unit}",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--no-ask-password",
        "--expand-environment=no",
        "--service-type=exec",
        "--property=ExitType=cgroup",
        f"--property=RuntimeMaxSec={math.ceil(float(limits.runtime_sec))}s",
        f"--property=TimeoutStopSec={math.ceil(float(limits.stop_sec))}s",
        "--property=KillMode=control-group",
        "--property=SendSIGKILL=yes",
        "--property=OOMPolicy=kill",
        f"--property=TasksMax={limits.tasks_max}",
        f"--property=MemoryHigh={limits.memory_high_bytes}",
        f"--property=MemoryMax={limits.memory_max_bytes}",
        f"--property=MemorySwapMax={limits.memory_swap_max_bytes}",
        f"--property=CPUQuota={limits.cpu_quota_percent}%",
        "--property=UMask=0077",
        "--property=NoNewPrivileges=yes",
        "--property=CollectMode=inactive-or-failed",
    ]
    environment_keys = _validated_environment_keys(spec.environment_file)
    if spec.environment_file is not None:
        command.append(f"--property=EnvironmentFile={spec.environment_file.as_posix()}")
    if spec.user is not None:
        command.append(f"--uid={spec.user}")
    if spec.working_directory == "%h":
        command.append("--property=WorkingDirectory=%h")
    else:
        command.append(f"--working-directory={spec.working_directory}")
    command.extend(
        (
            "--",
            os.fspath(_WRAPPER_PYTHON),
            "-I",
            "-B",
            "-S",
            "-c",
            _EXEC_EXACT_ENVIRONMENT,
            json.dumps(environment_keys, ensure_ascii=True, separators=(",", ":")),
            *spec.argv,
        )
    )
    return tuple(command)


def spawn_systemd_unit(
    spec: ManagedProcessSpec,
    unit: str,
) -> subprocess.Popen[bytes]:
    """验证固定程序后，以双管道启动 systemd-run 客户端。"""
    for executable in (_SYSTEMD_RUN, _SYSTEMCTL, _WRAPPER_PYTHON):
        verify_root_controlled_executable(executable)
    command = build_systemd_run_command(spec, unit)
    client_environment = {
        "PATH": os.defpath,
        "LC_ALL": "C.UTF-8",
    }
    return popen_tree(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd="/",
        env=client_environment,
        bufsize=0,
        close_fds=True,
    )


@contextmanager
def acquire_systemd_unit_lock(unit: str, timeout_sec: float) -> Iterator[None]:
    """用 root 私有、随控制器死亡自动释放的 flock 串行同一 slot。"""
    _require_unit(unit)
    if not _positive_finite(timeout_sec):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    import fcntl

    root_descriptor = -1
    descriptor = -1
    try:
        root_descriptor = _open_lock_root()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(
            f"{unit}.lock",
            flags,
            0o600,
            dir_fd=root_descriptor,
        )
        _require_lock_file(descriptor)
        deadline = time.monotonic() + float(timeout_sec)
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None
                time.sleep(min(remaining, 0.01))
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        yield
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except RuntimeSystemdProcessError:
        raise
    except Exception:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if root_descriptor >= 0:
            try:
                os.close(root_descriptor)
            except OSError:
                pass


def settle_systemd_unit(unit: str, timeout_sec: float) -> None:
    """强制停止同名旧 unit，并证明 MainPID=0、cgroup 未填充。"""
    _require_unit(unit)
    if not _positive_finite(timeout_sec):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    verify_root_controlled_executable(_SYSTEMCTL)
    deadline = time.monotonic() + float(timeout_sec)
    failed = False
    try:
        before = _read_state(unit, deadline)
        if before[0] == "not-found":
            _require_stopped_state(before)
            return
        if before[1] != "inactive" or before[3] != 0:
            _systemctl(
                ("kill", "--kill-whom=all", "--signal=SIGKILL", unit),
                deadline,
            )
        _systemctl(("stop", unit), deadline)
        after = _read_state(unit, deadline)
        _require_stopped_state(after)
        if after[4]:
            events = _cgroup_events_path(after[4], unit)
            try:
                populated = parse_cgroup_events(events.read_bytes())
            except FileNotFoundError:
                populated = False
            if populated:
                raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
        if after[0] != "not-found":
            _systemctl(("reset-failed", unit), deadline)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None


def abort_systemd_run_client(
    process: subprocess.Popen[bytes],
    timeout_sec: float,
) -> None:
    """只清算仍存活的 systemd-run 客户端，不把旧 PID 当作进程组。"""
    if not _positive_finite(timeout_sec):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    deadline = time.monotonic() + float(timeout_sec)
    failed = False
    try:
        if process.poll() is None:
            kill_process_tree(process, timeout=max(0.0, deadline - time.monotonic()))
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired):
            failed = True
        if process.poll() is None:
            failed = True
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
    if failed:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None


def _validated_environment_keys(environment_file: Path | None) -> tuple[str, ...]:
    if environment_file is None:
        return ()
    try:
        literal = require_systemd_environment_file_path(environment_file.as_posix())
        if literal != environment_file.as_posix():
            raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
        keys = validate_systemd_environment_file(environment_file)
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except (SystemdEnvironmentFileError, SystemdEnvironmentFilePathError):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None
    except RuntimeSystemdProcessError:
        raise
    except Exception:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE) from None
    if type(keys) is not frozenset or any(type(key) is not str for key in keys):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    return tuple(sorted(keys))


def _open_lock_root() -> int:
    try:
        os.mkdir(_LOCK_ROOT, 0o700)
    except FileExistsError:
        pass
    metadata = _LOCK_ROOT.lstat()
    expected_uid = os.geteuid() if hasattr(os, "geteuid") else 0
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or _LOCK_ROOT.resolve(strict=True) != _LOCK_ROOT
    ):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(_LOCK_ROOT, flags)


def _require_lock_file(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    expected_uid = os.geteuid() if hasattr(os, "geteuid") else 0
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)


def _read_state(unit: str, deadline: float) -> tuple[str, str, str, int, str]:
    timeout = _remaining_positive(deadline, maximum=10.0)
    result = subprocess.run(
        (
            os.fspath(_SYSTEMCTL),
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=MainPID",
            "--property=ControlGroup",
        ),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=timeout,
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > _MAX_STATE_BYTES:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    expected = {"LoadState", "ActiveState", "SubState", "MainPID", "ControlGroup"}
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in expected or key in values or "\x00" in value:
            raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
        values[key] = value
    if set(values) != expected or not values["MainPID"].isdigit():
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    return (
        values["LoadState"],
        values["ActiveState"],
        values["SubState"],
        int(values["MainPID"]),
        values["ControlGroup"],
    )


def _systemctl(arguments: tuple[str, ...], deadline: float) -> None:
    result = subprocess.run(
        (os.fspath(_SYSTEMCTL), *arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=_remaining_positive(deadline, maximum=30.0),
        env={"PATH": os.defpath, "LC_ALL": "C.UTF-8"},
    )
    if result.returncode != 0:
        state = _read_state(arguments[-1], deadline)
        _require_stopped_state(state)


def _require_stopped_state(state: tuple[str, str, str, int, str]) -> None:
    load, active, sub, pid, control_group = state
    if load == "not-found":
        if active != "inactive" or sub != "dead" or pid != 0 or control_group:
            raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
        return
    if load != "loaded" or active != "inactive" or sub != "dead" or pid != 0:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)


def _cgroup_events_path(control_group: str, unit: str) -> Path:
    parts = control_group.split("/")
    segments = parts[1:] if parts[:1] == [""] else []
    if (
        len(segments) < 2
        or segments[-1] != unit
        or any(
            segment in {"", ".", ".."} or _CGROUP_PART.fullmatch(segment) is None
            for segment in segments
        )
    ):
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    return _CGROUP_ROOT.joinpath(*segments, "cgroup.events")


def _require_spec_and_unit(spec: object, unit: object) -> None:
    if type(spec) is not ManagedProcessSpec:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    _require_unit(unit)


def _require_unit(unit: object) -> None:
    if type(unit) is not str or _UNIT.fullmatch(unit) is None:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)


def _positive_finite(value: object) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def _remaining_positive(deadline: float, *, maximum: float) -> float:
    remaining = min(maximum, deadline - time.monotonic())
    if remaining <= 0:
        raise RuntimeSystemdProcessError(_ERROR_MESSAGE)
    return remaining


__all__ = [
    "RuntimeSystemdProcessError",
    "abort_systemd_run_client",
    "acquire_systemd_unit_lock",
    "build_systemd_run_command",
    "settle_systemd_unit",
    "spawn_systemd_unit",
]
