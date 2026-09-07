"""先进入指定 cgroup，再等待父进程放行并 exec 目标。"""

from __future__ import annotations

import ctypes
import os
import re
import signal
import sys
from collections.abc import Sequence
from pathlib import Path

_FAILURE_RC = 125
_ATTEMPT_NAME_RE = re.compile(r"attempt-[0-9a-f]{64}\Z")
_MAX_MEMBERS_BYTES = 1024 * 1024
_PR_SET_PDEATHSIG = 1
_BOOTSTRAP_PATH = Path(__file__).resolve(strict=True)


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


def build_bootstrap_command(
    command: Sequence[str],
    target: Path,
    ready_fd: int,
    expected_parent_pid: int,
) -> list[str]:
    """构造直接执行当前 bootstrap 文件的绝对 argv。"""
    return [
        sys.executable,
        "-I",
        "-S",
        str(_BOOTSTRAP_PATH),
        "--cgroup",
        str(target),
        "--ready-fd",
        str(ready_fd),
        "--expected-parent-pid",
        str(expected_parent_pid),
        "--",
        *command,
    ]


def _arguments() -> tuple[Path, int, int, list[str]] | None:
    args = sys.argv[1:]
    if len(args) < 8 or args[0] != "--cgroup" or args[2] != "--ready-fd":
        return None
    if args[4] != "--expected-parent-pid" or args[6] != "--":
        return None
    try:
        ready_fd = int(args[3])
        expected_parent_pid = int(args[5])
    except ValueError:
        return None
    command = args[7:]
    if ready_fd < 3 or expected_parent_pid <= 0 or not command or not os.path.isabs(command[0]):
        return None
    return Path(args[1]), ready_fd, expected_parent_pid, command


def _arm_parent_guard(expected_parent_pid: int) -> bool:
    """先设置内核死亡信号，再复核父 PID 以封闭设置前竞态。"""
    return _set_parent_death_signal() and os.getppid() == expected_parent_pid


def _resolved_target(requested: Path) -> Path | None:
    try:
        root = Path(os.environ["CODEV_REINDEX_CGROUP_ROOT"]).resolve(strict=True)
        target = requested.resolve(strict=True)
    except (KeyError, OSError):
        return None
    if target.parent != root or _ATTEMPT_NAME_RE.fullmatch(target.name) is None:
        return None
    return target


def _assign_self(target: Path) -> bool:
    pid = os.getpid()
    try:
        (target / "cgroup.procs").write_text(str(pid), encoding="ascii")
        with (target / "cgroup.procs").open("rb") as stream:
            members = stream.read(_MAX_MEMBERS_BYTES + 1)
    except OSError:
        return False
    if len(members) > _MAX_MEMBERS_BYTES:
        return False
    try:
        member_pids = {int(value) for value in members.split()}
    except ValueError:
        return False
    return pid in member_pids


def _wait_for_parent(ready_fd: int) -> bool:
    try:
        return os.read(ready_fd, 2) == b"1"
    except OSError:
        return False
    finally:
        try:
            os.close(ready_fd)
        except OSError:
            pass


def main() -> int:
    parsed = _arguments()
    if parsed is None:
        return _FAILURE_RC
    requested, ready_fd, expected_parent_pid, command = parsed
    if not _arm_parent_guard(expected_parent_pid):
        return _FAILURE_RC
    target = _resolved_target(requested)
    if target is None or not _assign_self(target):
        return _FAILURE_RC
    if not _wait_for_parent(ready_fd):
        return _FAILURE_RC
    try:
        os.execv(command[0], command)
    except OSError:
        return _FAILURE_RC
    return _FAILURE_RC


if __name__ == "__main__":
    raise SystemExit(main())
