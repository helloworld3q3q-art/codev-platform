"""在父进程固化 Linux 身份后才 exec 目标的最小 POSIX 启动门。"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

_FAILURE_RC = 125
_PR_SET_PDEATHSIG = 1
_BOOTSTRAP_PATH = Path(__file__).resolve(strict=True)


def build_bootstrap_command(
    command: Sequence[str],
    ready_fd: int,
    expected_parent_pid: int,
) -> list[str]:
    """用隔离解释器模式构造绝对 bootstrap argv。"""
    return [
        sys.executable,
        "-I",
        "-S",
        str(_BOOTSTRAP_PATH),
        "--ready-fd",
        str(ready_fd),
        "--expected-parent-pid",
        str(expected_parent_pid),
        "--",
        *command,
    ]


def _set_parent_death_signal() -> bool:
    """只用标准库设置 Linux 父进程死亡信号。"""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = (
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        )
        prctl.restype = ctypes.c_int
        result = prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except (AttributeError, OSError):
        return False
    return result == 0


def _arguments() -> tuple[int, int, list[str]] | None:
    args = sys.argv[1:]
    if len(args) < 6 or args[0] != "--ready-fd":
        return None
    if args[2] != "--expected-parent-pid" or args[4] != "--":
        return None
    try:
        ready_fd = int(args[1])
        expected_parent_pid = int(args[3])
    except ValueError:
        return None
    command = args[5:]
    if ready_fd < 3 or expected_parent_pid <= 0 or not command or not os.path.isabs(command[0]):
        return None
    return ready_fd, expected_parent_pid, command


def _arm_parent_guard(expected_parent_pid: int) -> bool:
    """先设置内核死亡信号，再复核父 PID 以封闭设置前竞态。"""
    return _set_parent_death_signal() and os.getppid() == expected_parent_pid


def main() -> int:
    parsed = _arguments()
    if parsed is None:
        return _FAILURE_RC
    ready_fd, expected_parent_pid, command = parsed
    if not _arm_parent_guard(expected_parent_pid):
        return _FAILURE_RC
    try:
        token = os.read(ready_fd, 2)
    except OSError:
        return _FAILURE_RC
    finally:
        try:
            os.close(ready_fd)
        except OSError:
            pass
    if token != b"1":
        return _FAILURE_RC
    try:
        os.execv(command[0], command)
    except OSError:
        return _FAILURE_RC
    return _FAILURE_RC


if __name__ == "__main__":
    raise SystemExit(main())
