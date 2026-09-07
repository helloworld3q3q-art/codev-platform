"""CodeGraph 有界后端启动协调测试。"""
from __future__ import annotations

import asyncio

import pytest


def test_启动超时会取消任务且在确认结束前拒绝复用() -> None:
    from codev_platform.codegraph.backend_startup import (
        BoundedBackendStartup,
        CodegraphBackendStartupError,
    )

    cancelled = asyncio.Event()

    async def 永不就绪() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def 放行() -> None:
        return None

    startup = BoundedBackendStartup(
        永不就绪,
        startup_timeout_sec=0.01,
        cancel_timeout_sec=0.1,
    )

    async def 运行() -> None:
        with pytest.raises(CodegraphBackendStartupError, match="启动超时"):
            await startup.ensure(admit=放行)
        await asyncio.wait_for(cancelled.wait(), timeout=0.1)
        assert startup.task is not None
        assert startup.task.done()
        assert startup.alive is False

    asyncio.run(运行())


def test_并发调用复用同一启动任务并共享有界失败() -> None:
    from codev_platform.codegraph.backend_startup import (
        BoundedBackendStartup,
        CodegraphBackendStartupError,
    )

    started = 0

    async def 永不就绪() -> None:
        nonlocal started
        started += 1
        await asyncio.Event().wait()

    async def 放行() -> None:
        return None

    startup = BoundedBackendStartup(
        永不就绪,
        startup_timeout_sec=0.01,
        cancel_timeout_sec=0.1,
    )

    async def 运行() -> None:
        results = await asyncio.gather(
            startup.ensure(admit=放行),
            startup.ensure(admit=放行),
            return_exceptions=True,
        )
        assert started == 1
        assert all(isinstance(result, CodegraphBackendStartupError) for result in results)

    asyncio.run(运行())


def test_已确认结束后下一次启动可以重新创建任务() -> None:
    from codev_platform.codegraph.backend_startup import BoundedBackendStartup

    attempts = 0
    release = asyncio.Event()
    startup: BoundedBackendStartup

    async def 启动任务() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()
            return
        startup.mark_ready()
        await release.wait()

    async def 放行() -> None:
        return None

    startup = BoundedBackendStartup(
        启动任务,
        startup_timeout_sec=0.01,
        cancel_timeout_sec=0.1,
    )

    async def 运行() -> None:
        with pytest.raises(RuntimeError, match="启动超时"):
            await startup.ensure(admit=放行)
        await startup.ensure(admit=放行)
        assert attempts == 2
        release.set()
        assert startup.task is not None
        await asyncio.wait_for(startup.task, timeout=0.1)

    asyncio.run(运行())


def test_启动准入有界且超时后允许重试() -> None:
    from codev_platform.codegraph.backend_startup import (
        BoundedBackendStartup,
        CodegraphBackendStartupError,
    )

    attempts = 0
    release = asyncio.Event()
    startup: BoundedBackendStartup

    async def 工作任务() -> None:
        startup.mark_ready()
        await release.wait()

    async def 准入() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()

    startup = BoundedBackendStartup(
        工作任务,
        startup_timeout_sec=0.01,
        cancel_timeout_sec=0.1,
    )

    async def 运行() -> None:
        with pytest.raises(CodegraphBackendStartupError, match="准入超时"):
            await asyncio.wait_for(startup.ensure(admit=准入), timeout=0.1)
        assert startup.task is None
        await startup.ensure(admit=准入)
        assert attempts == 2
        release.set()
        assert startup.task is not None
        await asyncio.wait_for(startup.task, timeout=0.1)

    asyncio.run(运行())
