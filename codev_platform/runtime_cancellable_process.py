"""运行时依赖准备使用的可取消、可回收子进程端口。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
import subprocess
import threading
import time


class RuntimeCancellableProcessError(RuntimeError):
    """子进程未成功完成；异常永不携带命令或环境内容。"""


class RuntimeProcessCancelled(RuntimeCancellableProcessError):
    """上游失败后，当前子进程已被取消并回收。"""


class RuntimeProcessTimedOut(RuntimeCancellableProcessError):
    """子进程超过自身截止时间。"""


class RuntimeProcessFailed(RuntimeCancellableProcessError):
    """子进程以非零状态退出或无法启动。"""


class RuntimeCancellation:
    """同一批任务共享的单向取消信号。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


def run_cancellable_process(
    command: Sequence[str],
    *,
    cancellation: RuntimeCancellation,
    timeout: float,
    environment: Mapping[str, str],
    poll_interval: float = 0.1,
) -> None:
    """静默运行命令；取消或超时会终止整个独立进程组并完成回收。"""
    _validate_inputs(command, cancellation, timeout, environment, poll_interval)
    if cancellation.cancelled:
        raise RuntimeProcessCancelled("运行时子进程已取消")
    try:
        process = subprocess.Popen(
            tuple(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(environment),
            start_new_session=os.name == "posix",
        )
    except OSError:
        raise RuntimeProcessFailed("运行时子进程无法启动") from None
    deadline = time.monotonic() + float(timeout)
    while True:
        return_code = process.poll()
        if return_code is not None:
            if return_code != 0:
                raise RuntimeProcessFailed("运行时子进程执行失败")
            return
        remaining = deadline - time.monotonic()
        if cancellation.cancelled:
            _terminate_and_reap(process)
            raise RuntimeProcessCancelled("运行时子进程已取消")
        if remaining <= 0:
            _terminate_and_reap(process)
            raise RuntimeProcessTimedOut("运行时子进程执行超时")
        cancellation.wait(min(float(poll_interval), remaining))


def _validate_inputs(
    command: Sequence[str],
    cancellation: RuntimeCancellation,
    timeout: float,
    environment: Mapping[str, str],
    poll_interval: float,
) -> None:
    if (
        isinstance(command, (str, bytes))
        or not command
        or any(type(value) is not str or not value for value in command)
    ):
        raise TypeError("运行时子进程命令无效")
    if not isinstance(cancellation, RuntimeCancellation):
        raise TypeError("运行时子进程取消信号无效")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("运行时子进程超时无效")
    if not isinstance(environment, Mapping) or any(
        type(key) is not str or type(value) is not str
        for key, value in environment.items()
    ):
        raise TypeError("运行时子进程环境无效")
    if not isinstance(poll_interval, (int, float)) or poll_interval <= 0:
        raise ValueError("运行时子进程轮询间隔无效")


def _terminate_and_reap(process: subprocess.Popen[bytes]) -> None:
    _signal_process(process, force=False)
    try:
        process.wait(timeout=1.0)
        return
    except subprocess.TimeoutExpired:
        pass
    _signal_process(process, force=True)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        raise RuntimeCancellableProcessError("运行时子进程无法回收") from None


def _signal_process(process: subprocess.Popen[bytes], *, force: bool) -> None:
    try:
        if os.name == "posix":
            import signal

            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif not force:
            process.terminate()
        else:
            process.kill()
    except OSError:
        return


__all__ = [
    "RuntimeCancellation",
    "RuntimeCancellableProcessError",
    "RuntimeProcessCancelled",
    "RuntimeProcessFailed",
    "RuntimeProcessTimedOut",
    "run_cancellable_process",
]
