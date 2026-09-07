"""cgroup 后端当前进程内的最小可变状态。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(slots=True)
class LiveCgroupProcess:
    process: subprocess.Popen | None
    activation_fd: int | None


__all__ = ["LiveCgroupProcess"]
