"""可取消运行时子进程的终止与回收测试。"""

from __future__ import annotations

import sys
import threading
import time

import pytest

from codev_platform.runtime_cancellable_process import (
    RuntimeCancellation,
    RuntimeProcessCancelled,
    run_cancellable_process,
)
from codev_platform.runtime_process import isolated_process_environment


def test_cancellation_terminates_and_reaps_long_running_process() -> None:
    cancellation = RuntimeCancellation()
    timer = threading.Timer(0.1, cancellation.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(RuntimeProcessCancelled):
            run_cancellable_process(
                (sys.executable, "-I", "-c", "import time; time.sleep(30)"),
                cancellation=cancellation,
                timeout=10,
                environment=isolated_process_environment(),
                poll_interval=0.01,
            )
    finally:
        timer.cancel()

    assert time.monotonic() - started < 2
