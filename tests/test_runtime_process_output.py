"""受管进程双管道字节预算测试。"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from codev_platform.core.process_tree import popen_tree
from codev_platform.runtime_process_output import (
    RuntimeProcessOutputError,
    collect_process_output,
)


@pytest.mark.skipif(os.name != "posix", reason="selector 管道测试需要 POSIX")
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("size", [4096, 4097])
def test_stdout与stderr分别执行精确硬边界(stream: str, size: int) -> None:
    script = f"import sys; sys.{stream}.buffer.write(b'x'*{size}); sys.{stream}.flush()"
    process = popen_tree(
        (sys.executable, "-I", "-B", "-c", script),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    if size == 4097:
        with pytest.raises(RuntimeProcessOutputError):
            collect_process_output(process, 4096, 4096, time.monotonic() + 5.0)
        process.kill()
        process.wait(timeout=2.0)
        return

    result = collect_process_output(process, 4096, 4096, time.monotonic() + 5.0)
    assert len(getattr(result, stream)) == 4096


@pytest.mark.skipif(os.name != "posix", reason="selector 管道测试需要 POSIX")
def test_双流同时洪泛不会互相阻塞且每路最多保留预算加一字节() -> None:
    script = "import os; [(os.write(1,b'x'*1024),os.write(2,b'y'*1024)) for _ in range(32)]"
    process = popen_tree(
        (sys.executable, "-I", "-B", "-c", script),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    with pytest.raises(RuntimeProcessOutputError) as caught:
        collect_process_output(process, 4096, 4096, time.monotonic() + 5.0)

    assert caught.value.stdout_size <= 4097
    assert caught.value.stderr_size <= 4097
    process.kill()
    process.wait(timeout=2.0)
