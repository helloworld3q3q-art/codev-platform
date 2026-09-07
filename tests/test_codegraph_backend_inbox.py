"""CodeGraph 可排空后端收件箱测试。"""
from __future__ import annotations

import asyncio

import pytest


def test_关闭状态拒绝新请求() -> None:
    from codev_platform.codegraph.backend_inbox import (
        BackendInbox,
        CodegraphBackendDrainingError,
    )

    async def 运行() -> None:
        inbox = BackendInbox()
        await inbox.open()
        await inbox.close()

        with pytest.raises(CodegraphBackendDrainingError, match="排空"):
            await inbox.submit("关闭后的请求")

    asyncio.run(运行())


def test_关闭会用统一暂态异常拒绝全部排队请求() -> None:
    from codev_platform.codegraph.backend_inbox import (
        BackendInbox,
        CodegraphBackendDrainingError,
    )

    async def 运行() -> None:
        inbox = BackendInbox()
        await inbox.open()
        requests = [
            asyncio.create_task(inbox.submit(f"请求-{index}"))
            for index in range(3)
        ]
        await asyncio.sleep(0)

        await inbox.close()

        results = await asyncio.gather(*requests, return_exceptions=True)
        assert all(isinstance(result, CodegraphBackendDrainingError) for result in results)
        assert {str(result) for result in results} == {"CodeGraph 后端正在排空，暂不接收请求"}

    asyncio.run(运行())


def test_关闭会唤醒正在等待请求的消费者() -> None:
    from codev_platform.codegraph.backend_inbox import (
        BackendInbox,
        CodegraphBackendDrainingError,
    )

    async def 运行() -> None:
        inbox = BackendInbox()
        await inbox.open()
        consumer = asyncio.create_task(inbox.next())
        await asyncio.sleep(0)

        await inbox.close()

        with pytest.raises(CodegraphBackendDrainingError, match="排空"):
            await asyncio.wait_for(consumer, timeout=0.1)

    asyncio.run(运行())


def test_重新开放使用全新队列且旧请求不会泄漏() -> None:
    from codev_platform.codegraph.backend_inbox import (
        BackendInbox,
        CodegraphBackendDrainingError,
    )

    async def 运行() -> None:
        inbox = BackendInbox()
        await inbox.open()
        old_request = asyncio.create_task(inbox.submit("旧请求"))
        await asyncio.sleep(0)
        await inbox.close()
        await inbox.open()

        new_request = asyncio.create_task(inbox.submit("新请求"))
        await asyncio.sleep(0)
        payload, result = await asyncio.wait_for(inbox.next(), timeout=0.1)

        assert payload == "新请求"
        result.set_result("完成")
        assert await new_request == "完成"
        with pytest.raises(CodegraphBackendDrainingError):
            await old_request

    asyncio.run(运行())


def test_重置会拒绝旧请求并回到关闭状态() -> None:
    from codev_platform.codegraph.backend_inbox import (
        BackendInbox,
        CodegraphBackendDrainingError,
    )

    async def 运行() -> None:
        inbox = BackendInbox()
        await inbox.open()
        old_request = asyncio.create_task(inbox.submit("旧请求"))
        await asyncio.sleep(0)

        await inbox.reset()

        with pytest.raises(CodegraphBackendDrainingError):
            await old_request
        with pytest.raises(CodegraphBackendDrainingError):
            await inbox.submit("重置后的请求")

    asyncio.run(运行())


def test_写意图轮询仅在探针返回真时完成() -> None:
    from codev_platform.codegraph.backend_inbox import wait_for_writer_intent

    async def 运行() -> None:
        outcomes = iter((False, False, True))
        calls = 0

        def 探针() -> bool:
            nonlocal calls
            calls += 1
            return next(outcomes)

        await wait_for_writer_intent(探针, poll_sec=0)
        assert calls == 3

    asyncio.run(运行())


def test_写意图轮询支持异步探针() -> None:
    from codev_platform.codegraph.backend_inbox import wait_for_writer_intent

    async def 运行() -> None:
        outcomes = iter((False, True))

        async def 探针() -> bool:
            await asyncio.sleep(0)
            return next(outcomes)

        await wait_for_writer_intent(探针, poll_sec=0)

    asyncio.run(运行())


@pytest.mark.parametrize("异步探针", [False, True])
def test_写意图轮询原样传播探针异常(异步探针: bool) -> None:
    from codev_platform.codegraph.backend_inbox import wait_for_writer_intent

    async def 运行() -> None:
        expected = LookupError("探针失败")

        def 同步探针() -> bool:
            raise expected

        async def 异步探针函数() -> bool:
            raise expected

        check = 异步探针函数 if 异步探针 else 同步探针
        with pytest.raises(LookupError) as captured:
            await wait_for_writer_intent(check, poll_sec=0)
        assert captured.value is expected

    asyncio.run(运行())


def test_写意图轮询不吞取消() -> None:
    from codev_platform.codegraph.backend_inbox import wait_for_writer_intent

    async def 运行() -> None:
        task = asyncio.create_task(
            wait_for_writer_intent(lambda: False, poll_sec=60)
        )
        await asyncio.sleep(0)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(运行())
