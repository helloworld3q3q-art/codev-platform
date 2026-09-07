from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from typing import Any

_WINDOWS_GROUP_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
_WINDOWS_NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DEFAULT_CLEANUP_TIMEOUT_SEC = 2.0
_POSIX_GROUP_ATTRIBUTE = "_codev_platform_process_group_id"
_MAX_PROC_STAT_BYTES = 4096


def _bounded_timeout(value: float, field: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{field} 必须是有限非负数")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"{field} 必须是有限非负数")
    return timeout


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def popen_tree(
    args: Sequence[str] | str,
    *,
    no_window: bool = False,
    stdin: Any = subprocess.DEVNULL,
    **kwargs: Any,
) -> subprocess.Popen:
    popen_kwargs = dict(kwargs)
    if "stdin" not in popen_kwargs:
        popen_kwargs["stdin"] = stdin

    if os.name == "nt":
        creationflags = int(popen_kwargs.get("creationflags", 0)) | _WINDOWS_GROUP_FLAGS
        if no_window:
            creationflags |= _WINDOWS_NO_WINDOW_FLAG
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs.setdefault("start_new_session", True)

    process = subprocess.Popen(args, **popen_kwargs)
    if os.name != "nt" and popen_kwargs.get("start_new_session") is True:
        setattr(process, _POSIX_GROUP_ATTRIBUTE, process.pid)
    return process


def kill_process_tree(proc: subprocess.Popen, *, timeout: float = 5.0) -> None:
    timeout = _bounded_timeout(timeout, "timeout")
    returncode = proc.poll()
    if os.name == "nt":
        if returncode is not None:
            return
        _run_taskkill(proc.pid, timeout)
        if proc.poll() is None:
            _kill_root(proc)
        return

    if returncode is not None:
        group = getattr(proc, _POSIX_GROUP_ATTRIBUTE, None)
        if type(group) is int and group == proc.pid and sys.platform.startswith("linux"):
            _kill_reaped_linux_group(group, timeout)
        return

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        _kill_root(proc)


def _kill_reaped_linux_group(group: int, timeout: float) -> None:
    """父进程已回收时，用 pidfd 清算仍属于原 session 的后代。"""
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        return
    deadline = time.monotonic() + timeout
    while True:
        descriptors = _linux_process_group_pidfds(group)
        if descriptors is None:
            return
        if descriptors:
            for descriptor in descriptors:
                _kill_linux_pidfd(descriptor)
        elif not _linux_group_exists(group):
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(min(0.001, _remaining(deadline)))


def _linux_process_group_pidfds(group: int) -> tuple[int, ...] | None:
    """返回稳定成员 pidfd；出现同号新 group leader 时拒绝信号。"""
    if os.path.exists(f"/proc/{group}"):
        return None
    descriptors: list[int] = []
    try:
        entries = os.scandir("/proc")
    except OSError:
        return None
    try:
        with entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                before = _read_linux_process_identity(entry.name)
                if before is None:
                    continue
                pid, state, process_group, session, _start_time = before
                if state == "Z" or process_group != group or session != group:
                    continue
                try:
                    descriptor = os.pidfd_open(pid, 0)
                except OSError:
                    continue
                after = _read_linux_process_identity(entry.name)
                if after != before:
                    os.close(descriptor)
                    continue
                descriptors.append(descriptor)
    except OSError:
        for descriptor in descriptors:
            os.close(descriptor)
        return None
    if os.path.exists(f"/proc/{group}"):
        for descriptor in descriptors:
            os.close(descriptor)
        return None
    return tuple(descriptors)


def _read_linux_process_identity(name: str) -> tuple[int, str, int, int, int] | None:
    try:
        descriptor = os.open(
            f"/proc/{name}/stat",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError:
        return None
    try:
        raw = os.read(descriptor, _MAX_PROC_STAT_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_PROC_STAT_BYTES:
        return None
    marker = raw.rfind(b")")
    if marker <= 0:
        return None
    fields = raw[marker + 1 :].split()
    if len(fields) < 20 or len(fields[0]) != 1:
        return None
    try:
        return (
            int(name),
            fields[0].decode("ascii"),
            int(fields[2]),
            int(fields[3]),
            int(fields[19]),
        )
    except (UnicodeError, ValueError):
        return None


def _kill_linux_pidfd(descriptor: int) -> None:
    try:
        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _linux_group_exists(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _kill_root(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
    except OSError:
        return


def _run_taskkill(pid: int, timeout: float) -> None:
    """有界调用 taskkill；helper 卡住时只终止 helper，不做二次无界等待。"""
    try:
        helper = subprocess.Popen(  # noqa: S603,S607 - 固定系统工具，PID 来自可信 Popen。
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return
    try:
        helper.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_root(helper)


def _wait_after_error(proc: subprocess.Popen, deadline: float) -> None:
    try:
        proc.wait(timeout=_remaining(deadline))
    except (OSError, subprocess.TimeoutExpired):
        return


def _cleanup_after_timeout(
    proc: subprocess.Popen,
    *,
    original: subprocess.TimeoutExpired,
    deadline: float,
) -> tuple[Any, Any]:
    try:
        kill_process_tree(proc, timeout=_remaining(deadline))
    except Exception:  # noqa: BLE001 - 清理失败不得覆盖原始 timeout
        pass
    try:
        return proc.communicate(timeout=_remaining(deadline))
    except subprocess.TimeoutExpired as cleanup:
        stdout = cleanup.output if cleanup.output is not None else original.output
        stderr = cleanup.stderr if cleanup.stderr is not None else original.stderr
        raise subprocess.TimeoutExpired(
            proc.args,
            original.timeout,
            output=stdout,
            stderr=stderr,
        ) from None
    except Exception:
        raise subprocess.TimeoutExpired(
            proc.args,
            original.timeout,
            output=original.output,
            stderr=original.stderr,
        ) from None


def run_tree(
    args: Sequence[str] | str,
    *,
    input: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = False,
    check: bool = False,
    no_window: bool = False,
    cleanup_timeout_sec: float = _DEFAULT_CLEANUP_TIMEOUT_SEC,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    popen_kwargs = dict(kwargs)
    if input is not None:
        if "stdin" in popen_kwargs:
            raise ValueError("stdin 与 input 不能同时使用")
        popen_kwargs["stdin"] = subprocess.PIPE
    elif "stdin" not in popen_kwargs:
        popen_kwargs["stdin"] = subprocess.DEVNULL

    if capture_output:
        if "stdout" in popen_kwargs or "stderr" in popen_kwargs:
            raise ValueError("capture_output 不能与 stdout 或 stderr 同时使用")
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE

    cleanup_timeout = _bounded_timeout(cleanup_timeout_sec, "cleanup_timeout_sec")
    proc = popen_tree(args, no_window=no_window, **popen_kwargs)
    try:
        stdout, stderr = proc.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        deadline = time.monotonic() + cleanup_timeout
        stdout, stderr = _cleanup_after_timeout(proc, original=exc, deadline=deadline)
        raise subprocess.TimeoutExpired(
            proc.args,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from None
    except Exception:
        deadline = time.monotonic() + cleanup_timeout
        kill_process_tree(proc, timeout=_remaining(deadline))
        _wait_after_error(proc, deadline)
        raise

    completed = subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed
