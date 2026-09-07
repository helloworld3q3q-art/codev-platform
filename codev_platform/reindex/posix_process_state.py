"""POSIX 诊断后端当前进程内的最小可变状态。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from codev_platform.reindex.posix_process_identity import LinuxProcessInfo


@dataclass(slots=True)
class LivePosixProcess:
    process: subprocess.Popen | None
    root: LinuxProcessInfo
    activation_fd: int | None
    observed: dict[int, LinuxProcessInfo] = field(default_factory=dict)
    escaped: bool = False
    uncertain: bool = False


__all__ = ["LivePosixProcess"]
