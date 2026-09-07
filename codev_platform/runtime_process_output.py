"""POSIX 子进程双管道的流式字节预算采集。"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import selectors
import subprocess
import time


_ERROR_MESSAGE = "受管进程输出采集失败"


class RuntimeProcessOutputError(RuntimeError):
    """输出超时、超限或管道状态异常；只保留计数，不保留正文。"""

    def __init__(self, stdout_size: int = 0, stderr_size: int = 0) -> None:
        super().__init__(_ERROR_MESSAGE)
        self.stdout_size = stdout_size
        self.stderr_size = stderr_size


@dataclass(frozen=True, slots=True)
class ProcessOutput:
    """已完成进程的最小二进制结果，不持有命令或环境。"""

    returncode: int
    stdout: bytes
    stderr: bytes


def collect_process_output(
    process: subprocess.Popen[bytes],
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
    deadline: float,
) -> ProcessOutput:
    """并行排空 stdout/stderr，任一路只保留预算加一字节。"""
    stdout = bytearray()
    stderr = bytearray()
    failed = False
    result: ProcessOutput | None = None
    try:
        _validate_inputs(process, stdout_limit_bytes, stderr_limit_bytes, deadline)
        assert process.stdout is not None
        assert process.stderr is not None
        buffers = {"stdout": stdout, "stderr": stderr}
        limits = {
            "stdout": stdout_limit_bytes,
            "stderr": stderr_limit_bytes,
        }
        with selectors.DefaultSelector() as selector:
            for name, stream in (
                ("stdout", process.stdout),
                ("stderr", process.stderr),
            ):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeProcessOutputError(len(stdout), len(stderr))
                for key, _mask in selector.select(timeout=min(remaining, 0.25)):
                    name = key.data
                    stream = key.fileobj
                    allowance = limits[name] + 1 - len(buffers[name])
                    try:
                        block = os.read(stream.fileno(), max(1, min(64 * 1024, allowance)))
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not block:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    buffers[name].extend(block)
                    if len(buffers[name]) > limits[name]:
                        raise RuntimeProcessOutputError(len(stdout), len(stderr))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeProcessOutputError(len(stdout), len(stderr))
        returncode = process.wait(timeout=remaining)
        if type(returncode) is not int:
            raise RuntimeProcessOutputError(len(stdout), len(stderr))
        result = ProcessOutput(returncode, bytes(stdout), bytes(stderr))
    except (KeyboardInterrupt, SystemExit, MemoryError):
        raise
    except Exception:
        failed = True
    if failed or result is None:
        raise RuntimeProcessOutputError(len(stdout), len(stderr)) from None
    return result


def _validate_inputs(
    process: object,
    stdout_limit_bytes: object,
    stderr_limit_bytes: object,
    deadline: object,
) -> None:
    if (
        not isinstance(process, subprocess.Popen)
        or process.stdout is None
        or process.stderr is None
        or type(stdout_limit_bytes) is not int
        or stdout_limit_bytes < 0
        or type(stderr_limit_bytes) is not int
        or stderr_limit_bytes < 0
        or type(deadline) not in {int, float}
        or not math.isfinite(deadline)
    ):
        raise RuntimeProcessOutputError()


__all__ = [
    "ProcessOutput",
    "RuntimeProcessOutputError",
    "collect_process_output",
]
