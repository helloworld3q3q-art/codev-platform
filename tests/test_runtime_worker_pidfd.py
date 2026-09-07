"""root-fd worker pidfd 机械原语回归。"""

from __future__ import annotations

import os
import sys

import pytest

from codev_platform.runtime_worker_pidfd import (
    open_current_pidfd,
    open_process_pidfd,
    pidfd_is_ready,
    terminate_pidfd,
)


_LINUX_PIDFD = sys.platform.startswith("linux") and hasattr(os, "pidfd_open")


@pytest.mark.skipif(not _LINUX_PIDFD, reason="需要 Linux pidfd")
def test当前pidfd不可继承且未就绪() -> None:
    descriptor = open_current_pidfd()
    try:
        assert os.get_inheritable(descriptor) is False
        assert not pidfd_is_ready(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.skipif(not _LINUX_PIDFD, reason="需要 Linux pidfd")
def test精确pidfd可终止已认证子进程() -> None:
    process_id = os.fork()
    if process_id == 0:  # pragma: no cover - 子进程只验证内核 pidfd 终止。
        __import__("time").sleep(10)
        os._exit(0)
    descriptor = open_process_pidfd(process_id, "测试子进程")
    try:
        assert terminate_pidfd(descriptor)
        _wait_for_exit(process_id)
        assert pidfd_is_ready(descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _wait_for_exit(process_id: int) -> None:
    while True:
        try:
            observed, _status = os.waitpid(process_id, 0)
        except InterruptedError:
            continue
        if observed == process_id:
            return
