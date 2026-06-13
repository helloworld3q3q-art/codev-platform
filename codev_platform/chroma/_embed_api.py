"""共享 GPU 算力端点(/embed /rerank)+ GPU 算子收口助手 —— 从 server.py 分出(file-discipline §1)。

/embed /rerank 复用 daemon 已加载的 GPU 嵌入 / 重排模型, 给 agent-memory / code_vec 索引等
"想 embed 但不想再 load 第二份模型"的进程用(省第二份 → 不 OOM, 见 mcp GPU 教训)。GPU 并发
走同一信号量串行; 算子受 GPU_OP_TIMEOUT 收口(卡死时**释放信号量**返 503, 不永久持锁拖死整个
daemon + 在线 search_docs)。

**叶子模块**: 只依赖 _config / _models / _reranker / _helpers / _obslog 等叶子, **不 import
server**(单向, 无环, 区别于 impact_soft 当初的循环坑)。server.py `_run_http` 按 Route 注册
embed / rerank, 并末尾 re-export validate_* / _gpu_call / _release_cuda_cache 供测试 server.<name>。
"""
from __future__ import annotations

import asyncio

from starlette.responses import JSONResponse

from codev_platform.chroma._config import EMBED_ENCODE_BATCH, GPU_OP_TIMEOUT
from codev_platform.chroma._helpers import _is_gpu_error
from codev_platform.chroma._models import _ensure_model, _get_gpu_sem
from codev_platform.chroma._obslog import _flog
from codev_platform.chroma._reranker import _rerank_scores

# 触发 empty_cache 的最小批大小: 索引批(≥此值)清, 交互单/小查询不清(省其延迟)。
_GPU_CACHE_RELEASE_MIN_BATCH = 16


def _release_cuda_cache() -> None:
    """把 PyTorch CUDA 缓存分配器持有的**空闲**块还给驱动。

    缓存分配器为复用会留着 free 块不还系统; 大批量 + 变长 encode 累积碎片 → 显存涨满(实测
    daemon 涨到 7.7/8GB 后新 encode OOM 'AcceleratorError')。大 batch(索引)后 empty_cache
    去碎片, 防长跑膨胀。交互单条查询不触发(见调用处批大小门槛), 不增其延迟。"""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 — 清缓存失败不致命
        pass


async def _gpu_call(fn):
    """在线程跑同步 GPU 算子 (encode/rerank), 受 GPU_OP_TIMEOUT 收口。

    超时 → 抛 asyncio.TimeoutError, 调用方的 `async with _get_gpu_sem()` 随之退出 **释放信号量**,
    daemon 不会因一次卡死的算子永久持锁 (那会拖死整个 /embed + 在线 search_docs)。
    注: 超时只取消等待、释放锁; 后台线程仍跑完那次 encode (Python 不能强杀线程), 但锁已放,
    其余请求可继续。GPU_OP_TIMEOUT<=0 视为不限 (回退旧行为)。"""
    coro = asyncio.to_thread(fn)
    if GPU_OP_TIMEOUT and GPU_OP_TIMEOUT > 0:
        return await asyncio.wait_for(coro, timeout=GPU_OP_TIMEOUT)
    return await coro


# /embed /rerank 请求体校验: 纯函数(不碰模型/GPU/starlette), 模块级可单测。
# 返回 (parsed, None) 成功 / (None, err_msg) 失败 → handler 据 err 回 400。
def validate_embed_body(body: object) -> tuple[list[str] | None, str | None]:
    """{texts:[...]} 或 {text:"..."} → (texts, None);非法 → (None, err)。"""
    if not isinstance(body, dict):
        return None, "invalid json"
    texts = body.get("texts")
    if texts is None and body.get("text") is not None:
        texts = [body["text"]]
    if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
        return None, "texts (non-empty list[str]) required"
    return texts, None


def validate_rerank_body(body: object) -> tuple[tuple[str, list[str]] | None, str | None]:
    """{query:str, docs:[str]} → ((query,docs), None);非法 → (None, err)。"""
    if not isinstance(body, dict):
        return None, "invalid json"
    query = body.get("query")
    docs = body.get("docs")
    if not isinstance(query, str) or not isinstance(docs, list) or not docs \
            or not all(isinstance(d, str) for d in docs):
        return None, "query (str) + docs (non-empty list[str]) required"
    return (query, docs), None


async def embed(request):
    """共享嵌入端点: 复用 daemon 已加载的 GPU 嵌入模型(plain encode, 不加 query prompt, 与
    agent.embed.QwenLocalEmbedder 一致)。GPU 并发走同一信号量串行。鉴权同 /sse。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)
    texts, err = validate_embed_body(body)
    if err is not None:
        return JSONResponse({"error": err}, status_code=400)
    model = _ensure_model()
    if model is None:
        return JSONResponse({"error": "embedding model unavailable"}, status_code=503)
    try:
        # wait_for 收口: 一次 encode 卡住时 **释放 GPU 信号量** 返 503, 不让它永久持锁拖死
        # 整个 /embed(连带在线 search_docs)。云上大批量 remote 索引时这是稳定性硬前提。
        def _encode():
            return model.encode(texts, batch_size=EMBED_ENCODE_BATCH,
                                normalize_embeddings=True, convert_to_numpy=True)
        async with _get_gpu_sem():
            try:
                vecs = await _gpu_call(_encode)
            except Exception as enc_exc:  # noqa: BLE001
                # 反应式 OOM 回收: 撞 GPU OOM 时回收空闲块后**重试一次** —— 并发 search_docs 瞬时
                # 挤占常一次即缓解(batch_size 已封顶峰值, 这是叠加的尖峰兜底)。非 GPU 错原样上抛。
                if not _is_gpu_error(enc_exc):
                    raise
                _flog(f"[embed] GPU OOM (texts={len(texts)}); empty_cache 后重试一次")
                await asyncio.to_thread(_release_cuda_cache)
                vecs = await _gpu_call(_encode)
            if len(texts) >= _GPU_CACHE_RELEASE_MIN_BATCH:   # 索引大批后清缓存防膨胀 OOM
                await asyncio.to_thread(_release_cuda_cache)
        return JSONResponse({"vectors": [v.tolist() for v in vecs]})
    except asyncio.TimeoutError:
        _flog(f"[embed] GPU op 超时 (>{GPU_OP_TIMEOUT}s, texts={len(texts)}); 释放信号量返 503")
        return JSONResponse({"error": "embed timeout"}, status_code=503)
    except Exception as exc:  # noqa: BLE001
        # GPU OOM(含重试后仍 OOM)→ 回收空闲块 + 503 可重试: 让 RemoteEmbedder/worker 重发,
        # 不把一次瞬时显存尖峰升级成整个 code_vec build 失败(500 不可重试)。非 GPU 错才 500。
        if _is_gpu_error(exc):
            await asyncio.to_thread(_release_cuda_cache)
            _flog(f"[embed] GPU OOM 重试后仍失败 (texts={len(texts)}); empty_cache 后返 503 可重试")
            return JSONResponse({"error": "embed oom, retry"}, status_code=503)
        return JSONResponse({"error": f"embed failed: {type(exc).__name__}"}, status_code=500)


async def rerank(request):
    """共享重排端点: 复用 daemon 已加载的 Qwen3-Reranker(yes/no logits 打分), 给 agent-memory 等
    复用, 不再 load 第二份 reranker。body {query, docs} → {scores}(yes 概率)。鉴权同 /sse。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid json"}, status_code=400)
    parsed, err = validate_rerank_body(body)
    if err is not None:
        return JSONResponse({"error": err}, status_code=400)
    query, docs = parsed
    try:
        async with _get_gpu_sem():
            scores = await _gpu_call(lambda: _rerank_scores(query, docs))
            if len(docs) >= _GPU_CACHE_RELEASE_MIN_BATCH:   # 大候选集重排后清缓存防膨胀
                await asyncio.to_thread(_release_cuda_cache)
    except asyncio.TimeoutError:
        _flog(f"[rerank] GPU op 超时 (>{GPU_OP_TIMEOUT}s, docs={len(docs)}); 释放信号量返 503")
        return JSONResponse({"error": "rerank timeout"}, status_code=503)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"rerank failed: {type(exc).__name__}"}, status_code=500)
    if scores is None:
        return JSONResponse({"error": "reranker unavailable"}, status_code=503)
    return JSONResponse({"scores": scores})
