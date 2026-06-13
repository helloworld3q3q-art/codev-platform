"""daemon /embed /rerank GPU 算子超时收口: 一次卡死的 encode 不再永久持锁拖死 daemon。

根因事故: `async with gpu_sem: await to_thread(encode)` —— encode 卡住则信号量永不释放,
/embed 整体死锁, 连带打挂在线 search_docs(实测大批量索引触发, 重启才恢复)。
修法: to_thread 包 wait_for(GPU_OP_TIMEOUT), 超时抛 TimeoutError → async with 退出释放锁。
"""
from __future__ import annotations

import asyncio

import pytest

import codev_platform.chroma._embed_api as srv  # _gpu_call/_release_cuda_cache/GPU_OP_TIMEOUT 2026-06-14 抽到 _embed_api


def test_gpu_call_returns_result_normally():
    async def go():
        return await srv._gpu_call(lambda: 1 + 1)
    assert asyncio.run(go()) == 2


def test_release_cuda_cache_never_raises():
    # torch 缺 / 无 cuda 都不得抛(防御性: 清缓存失败不致命)
    srv._release_cuda_cache()   # 直接调一次, 不崩即通过


def test_gpu_call_times_out(monkeypatch):
    import time
    monkeypatch.setattr(srv, "GPU_OP_TIMEOUT", 0.2)

    async def go():
        return await srv._gpu_call(lambda: time.sleep(5))  # 远超 0.2s

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(go())


def test_gpu_call_no_timeout_when_disabled(monkeypatch):
    monkeypatch.setattr(srv, "GPU_OP_TIMEOUT", 0)  # <=0 视为不限

    async def go():
        return await srv._gpu_call(lambda: 42)
    assert asyncio.run(go()) == 42


def test_timed_out_gpu_op_releases_semaphore(monkeypatch):
    """核心断言: 卡死算子超时后, GPU 信号量被释放 → 下一个请求仍能拿到锁(不死锁)。"""
    import time
    monkeypatch.setattr(srv, "GPU_OP_TIMEOUT", 0.2)
    sem = asyncio.Semaphore(1)  # 复刻 daemon 的串行 GPU 信号量(并发=1)

    async def one_request(slow: bool):
        async with sem:
            if slow:
                await srv._gpu_call(lambda: time.sleep(5))  # 会超时
            else:
                await srv._gpu_call(lambda: "ok")
        return "done"

    async def go():
        # 第一个请求卡死超时(模拟坏 encode)
        with pytest.raises(asyncio.TimeoutError):
            await one_request(slow=True)
        # 关键: 锁必须已释放 —— 第二个请求能在合理时间内拿到锁完成(旧 bug 会永久阻塞)
        return await asyncio.wait_for(one_request(slow=False), timeout=2.0)

    assert asyncio.run(go()) == "done"
    assert not sem.locked()  # 锁已归还
