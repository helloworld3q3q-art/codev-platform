"""daemon /embed /rerank 算子超时收口：超时后禁止后台推理重叠放大。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

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


def test_timed_out_gpu_op_blocks_overlap_until_real_thread_finishes(monkeypatch):
    """等待超时不等于算子结束；旧线程存活时新请求必须快速失败，不能叠加推理。"""
    import threading

    monkeypatch.setattr(srv, "GPU_OP_TIMEOUT", 0.05)
    monkeypatch.setattr(srv, "_gpu_inflight_task", None, raising=False)
    started = threading.Event()
    release = threading.Event()
    fast_called = False

    def slow():
        started.set()
        release.wait(2.0)
        return "slow-done"

    def fast():
        nonlocal fast_called
        fast_called = True
        return "fast-done"

    async def go():
        with pytest.raises(asyncio.TimeoutError):
            await srv._gpu_call(slow)
        assert started.is_set()
        with pytest.raises(asyncio.TimeoutError):
            await srv._gpu_call(fast)
        assert fast_called is False

        release.set()
        for _ in range(100):
            task = srv._gpu_inflight_task
            if task is None or task.done():
                break
            await asyncio.sleep(0.01)
        return await srv._gpu_call(fast)

    assert asyncio.run(go()) == "fast-done"
    assert fast_called is True


def test_search_docs_embedding_and_rerank_share_gpu_inflight_guard(monkeypatch, tmp_path):
    """在线查询也必须进入同一推理占用门禁，不能绕过 /embed 的后台线程保护。"""
    from codev_platform.chroma import _tools

    model_file = tmp_path / "reranker"
    model_file.write_text("stub", encoding="utf-8")
    guarded_calls = 0
    encoded_calls = 0
    reranked_calls = 0

    class _Collection:
        def query(self, **_kwargs):
            return {
                "ids": [["doc-1"]],
                "documents": [["文档内容"]],
                "metadatas": [[{"file": "docs/a.md"}]],
                "distances": [[0.1]],
            }

    state = SimpleNamespace(
        collection=_Collection(),
        bm25_index=None,
        init_error=None,
        last_request_at=None,
        active_collection_name="demo__platform_docs",
    )

    async def guarded(operation):
        nonlocal guarded_calls
        guarded_calls += 1
        return operation()

    def encode(_query):
        nonlocal encoded_calls
        encoded_calls += 1
        return [0.1]

    def rerank(_query, docs):
        nonlocal reranked_calls
        reranked_calls += 1
        return [0.9] * len(docs)

    monkeypatch.setattr(_tools, "_ensure_project", lambda _pid: state)
    monkeypatch.setattr(_tools, "_gpu_call", guarded)
    monkeypatch.setattr(_tools, "_get_gpu_sem", lambda: asyncio.Semaphore(1))
    monkeypatch.setattr(_tools, "_encode_query", encode)
    monkeypatch.setattr(_tools, "_rerank_scores", rerank)
    monkeypatch.setattr(_tools, "RERANKER_ENABLED", True)
    monkeypatch.setattr(_tools, "RERANKER_MODEL", str(model_file))
    monkeypatch.setattr(_tools.rr, "_reranker_load_err", None)
    monkeypatch.setattr(_tools, "_flog", lambda _message: None)
    monkeypatch.setattr(_tools, "_log_recall", lambda _record: None)
    token = _tools._current_project_id.set("demo")
    try:
        result = asyncio.run(_tools.call_tool("search_docs", {"query": "测试", "k": 1}))
    finally:
        _tools._current_project_id.reset(token)

    assert result
    assert guarded_calls == 2
    assert encoded_calls == 1
    assert reranked_calls == 1
