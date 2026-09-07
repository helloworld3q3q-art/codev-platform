"""cgroup bootstrap 在登记前失败时的单预算事务回收。"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

from codev_platform.reindex.attempt_process import Deadline
from codev_platform.reindex.bootstrap_runtime import wait_for_false
from codev_platform.reindex.cgroup_v2 import CgroupFilesystem


def _remaining(expires_at: float) -> float:
    return max(0.0, expires_at - time.monotonic())


def _wait_process(process: subprocess.Popen, expires_at: float) -> bool:
    try:
        process.wait(timeout=_remaining(expires_at))
        return True
    except BaseException:
        return False


def _probe(populated: Callable[[str], bool | None], native_ref: str) -> bool | None:
    try:
        return populated(native_ref)
    except BaseException:
        return None


def abort_cgroup_bootstrap(
    *,
    filesystem: CgroupFilesystem,
    native_ref: str,
    process: subprocess.Popen | None,
    deadline: Deadline,
    populated: Callable[[str], bool | None],
    cleanup_sec: float,
) -> None:
    """同一截止点内清 cgroup，并终止尚未纳管的已知直接 bootstrap。"""
    expires_at = time.monotonic() + min(cleanup_sec, deadline.remaining())
    try:
        filesystem.kill(native_ref)
    except BaseException:
        pass
    if process is not None:
        try:
            process.kill()
        except BaseException:
            pass
    wait_for_false(lambda: _probe(populated, native_ref), _remaining(expires_at))
    if process is not None:
        _wait_process(process, expires_at)
    try:
        filesystem.remove_empty(native_ref)
    except BaseException:
        pass


__all__ = ["abort_cgroup_bootstrap"]
