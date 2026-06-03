"""chroma daemon —— embedding 模型 + chroma client 生命周期 (从 server.py 抽出, 无环 DAG)。

自带 5 个 rebound 全局 (_client / _model / _use_query_prompt / _global_init_error / _gpu_sem)
+ 4 个函数。从 `_config` 拿常量, **不依赖 server** → 无环 (§A.2b)。

rebound 全局是 **本模块私有真值源**: 函数内 `global _model` 等指本模块全局; 外部读者
(server.health / healthz / _tools.call_tool) 经 `import _models as m; m._model` 访问当前值,
不可 top-level `from import` (会捕获 import 期旧值 None 而 stale) —— 同 _reranker 模式。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from codev_platform.chroma._config import EMBED_MODEL, EMBED_DEVICE, DATA_DIR, GPU_CONCURRENCY
from codev_platform.chroma._helpers import _load_with_retry, _is_gpu_error
from codev_platform.chroma._obslog import _flog
from codev_platform.chroma._stats import _record_stat, _record_stat_error


_client = None
_model = None  # shared embedding model (multi-tenant: 同模型服务所有 project)
_use_query_prompt = False
_global_init_error: str | None = None  # model load failure (跨 project 共享)
_gpu_sem: asyncio.Semaphore | None = None  # lazy init in event loop


def _ensure_model():
    """Lazy 加载 SentenceTransformer 模型(只加载一次,hot-reload 不重载)。"""
    global _model, _use_query_prompt, _global_init_error
    if _model is not None:
        return _model
    if _global_init_error is not None:
        return None
    try:
        _flog(f"[init] model_path={EMBED_MODEL}")
        _flog(f"[init] device={EMBED_DEVICE} data_dir={DATA_DIR}")
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
            cuda_name = torch.cuda.get_device_name(0) if cuda_ok else "n/a"
            _flog(f"[init] torch={torch.__version__} cuda_available={cuda_ok} gpu={cuda_name}")
        except Exception as e:
            _flog(f"[init] torch import failed: {e}")

        from sentence_transformers import SentenceTransformer
        try:
            # 瞬时 GPU 失败 (busy / transient OOM) 先在 GPU 上有界重试退避; 耗尽再降级 CPU。
            _model = _load_with_retry(
                "embedding", lambda: SentenceTransformer(EMBED_MODEL, device=EMBED_DEVICE)
            )
        except Exception as gpu_exc:  # noqa: BLE001
            # F5: GPU 被其它进程抢占 (OOM / device busy) → 降级 CPU 重试一次, 不硬挂 daemon。
            # CPU 推理慢但可用, 避免整卡满时检索功能完全不可用。
            if EMBED_DEVICE != "cpu" and _is_gpu_error(gpu_exc):
                _flog(f"[init] WARN: GPU load failed after retries ({gpu_exc!s}), fallback to CPU (slower)")
                _model = SentenceTransformer(EMBED_MODEL, device="cpu")
            else:
                raise
        prompts = getattr(_model, "prompts", None) or {}
        _use_query_prompt = "query" in prompts and bool(prompts.get("query"))
        get_dim = _model.get_embedding_dimension if hasattr(_model, "get_embedding_dimension") else _model.get_sentence_embedding_dimension
        dim = get_dim()
        _flog(f"[init] model loaded dim={dim} max_seq={getattr(_model, 'max_seq_length', '?')} use_query_prompt={_use_query_prompt}")
        return _model
    except Exception as exc:  # noqa: BLE001
        _global_init_error = f"模型加载失败: {exc!s}"
        _flog(f"[init] ERROR: {_global_init_error}")
        return None


def _get_client():
    """Lazy chroma client (跨 project 共享单实例)."""
    global _client
    if _client is None:
        import chromadb  # lazy: heavy runtime 依赖, 顶层不 import (见 server 头注释)
        _client = chromadb.PersistentClient(path=str(DATA_DIR))
        from codev_platform.chroma import ensure_wal  # 写时 search 读不被锁 (默认 delete 模式会独占)
        ensure_wal(DATA_DIR)
    return _client


def _encode_query(query: str):
    """查询侧 encode:Qwen3 用 prompt_name='query',MiniLM 不用。"""
    kwargs: dict[str, Any] = {"normalize_embeddings": True, "convert_to_numpy": True}
    if _use_query_prompt:
        kwargs["prompt_name"] = "query"
    _t0 = time.perf_counter()
    try:
        vec = _model.encode([query], **kwargs)[0]
    except Exception as exc:
        _record_stat_error("embedding", exc)
        raise
    _record_stat("embedding", (time.perf_counter() - _t0) * 1000)
    return vec.tolist()


def _get_gpu_sem() -> asyncio.Semaphore:
    """Lazy init GPU semaphore (must be inside event loop)."""
    global _gpu_sem
    if _gpu_sem is None:
        _gpu_sem = asyncio.Semaphore(GPU_CONCURRENCY)
    return _gpu_sem
