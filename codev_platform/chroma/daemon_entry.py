"""Chroma daemon 轻量入口：竞争者胜出后才加载重量服务。"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

from codev_platform.chroma.spawn_lock import (
    acquire_spawn_lock_until,
    release_spawn_lock,
)
from codev_platform.core.runtime_artifacts import chroma_daemon_lifecycle_lock_path
from codev_platform.mcp_managed_service import require_managed_mcp_service


DAEMON_LIFECYCLE_LOCK_PATH = chroma_daemon_lifecycle_lock_path()
LIFECYCLE_LOCK_WAIT_TIMEOUT = float(os.getenv("PLATFORM_DOCS_DAEMON_LOCK_WAIT_TIMEOUT", "5"))


def _load_server_main() -> Callable[[], Awaitable[None]]:
    """锁定生命周期后再导入模型服务，失败竞争者不触发重量依赖。"""
    from codev_platform.chroma.server import main

    return main


def run() -> int:
    """取得生命周期所有权并运行服务；竞争失败返回 1。"""
    require_managed_mcp_service("chroma")
    lock = acquire_spawn_lock_until(
        DAEMON_LIFECYCLE_LOCK_PATH,
        LIFECYCLE_LOCK_WAIT_TIMEOUT,
    )
    if lock is None:
        print(
            "[platform-docs-daemon] 另一 daemon 已持有生命周期锁，本竞争者退出",
            file=sys.stderr,
            flush=True,
        )
        return 1
    try:
        asyncio.run(_load_server_main()())
        return 0
    finally:
        release_spawn_lock(lock)


if __name__ == "__main__":
    raise SystemExit(run())
