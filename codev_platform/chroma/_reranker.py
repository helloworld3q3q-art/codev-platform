"""chroma daemon —— Qwen3-Reranker 子系统 (从 server.py 抽出, file-discipline §1 + 无环 DAG)。

走 yes/no token logits (非 sentence-transformers CrossEncoder)。自带 reranker 全局, 从
`_config` 拿 RERANKER_* —— **不依赖 server**, 真正无环 (§A.2b 的 `_config` 叶子破循环成果)。

reranker 全局 (_reranker_tok/_model/_yes_id/_no_id/_load_err) 是 **rebound 标量**: 外部读者
(server.health / _tools) 经 `import _reranker as rr; rr._reranker_model` 访问当前值, 不可
top-level `from import` (会捕获 import 期旧值 None 而 stale)。
"""
from __future__ import annotations

import time
from pathlib import Path

from codev_platform.chroma._config import (
    RERANKER_ENABLED, RERANKER_MODEL, RERANKER_DEVICE, RERANKER_DTYPE,
)
from codev_platform.chroma._helpers import _torch_dtype, _load_with_retry
from codev_platform.chroma._obslog import _flog
from codev_platform.chroma._stats import _record_stat, _record_stat_error


# ---------- Reranker (Qwen3-Reranker-0.6B) ----------
# 走 yes/no token logits, 不是 sentence-transformers CrossEncoder
_reranker_tok = None
_reranker_model = None
_reranker_yes_id = None
_reranker_no_id = None
_reranker_load_err: str | None = None

_RERANKER_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based "
    "on the Query and the Instruct provided. Note that the answer can only be "
    "\"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
)
_RERANKER_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_RERANKER_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"


def _ensure_reranker():
    """Lazy 加载 Qwen3-Reranker. 模型 + tokenizer 一次性驻留 GPU.

    返回 (tok, model, yes_id, no_id) 或 None (未启用 / 加载失败).
    """
    global _reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id, _reranker_load_err
    if not RERANKER_ENABLED:
        return None
    if not RERANKER_MODEL or not Path(RERANKER_MODEL).exists():
        if _reranker_load_err is None:
            _reranker_load_err = f"reranker path missing: {RERANKER_MODEL}"
            _flog(f"[reranker] disabled: {_reranker_load_err}")
        return None
    if _reranker_load_err is not None:
        return None
    if _reranker_tok is not None and _reranker_model is not None:
        return (_reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id)
    try:
        import torch  # noqa: F401
        from transformers import AutoTokenizer, AutoModelForCausalLM
        _flog(f"[reranker] loading {RERANKER_MODEL} device={RERANKER_DEVICE}")
        _reranker_tok = AutoTokenizer.from_pretrained(RERANKER_MODEL, padding_side="left")
        dtype = _torch_dtype(RERANKER_DTYPE, RERANKER_DEVICE)

        def _load_reranker_model():
            mm = AutoModelForCausalLM.from_pretrained(RERANKER_MODEL, dtype=dtype)
            mm = mm.to(RERANKER_DEVICE)  # 尊重配置 device(cuda / cuda:N / cpu), 不再写死 .cuda()
            return mm.eval()

        # 瞬时 GPU 失败 (busy / transient OOM) 有界重试; 耗尽 → 抛 → 既有 except 进降级。
        m = _load_with_retry("reranker", _load_reranker_model)
        _reranker_model = m
        _reranker_yes_id = _reranker_tok.convert_tokens_to_ids("yes")
        _reranker_no_id = _reranker_tok.convert_tokens_to_ids("no")
        _flog(f"[reranker] loaded, yes_id={_reranker_yes_id} no_id={_reranker_no_id}")
        return (_reranker_tok, _reranker_model, _reranker_yes_id, _reranker_no_id)
    except Exception as exc:  # noqa: BLE001
        _reranker_load_err = f"{type(exc).__name__}: {exc}"
        _flog(f"[reranker] load FAIL: {_reranker_load_err}")
        return None


def _rerank_scores(query: str, docs: list[str]) -> list[float] | None:
    """对 (query, doc) 对打分. 返回 [0,1] 浮点分数 (yes 概率).
    Reranker 不可用时返回 None, 调用方降级走原 embedding 顺序.
    """
    pack = _ensure_reranker()
    if pack is None or not docs:
        return None
    tok, model, yes_id, no_id = pack
    try:
        import torch
        # 防御:截断对齐 CHUNK_HARD_MAX=1500(index_docs.py),避免 30 pair padded
        # sequence 拉满推理时延 +30~50%(2026-05-23 验证发现:>2000 字符 chunk 让
        # rerank P95 从 ~3s 升到 ~6.7s)
        prompts = [
            f"{_RERANKER_PREFIX}<Instruct>: {_RERANKER_INSTRUCT}\n<Query>: {query}\n<Document>: {d[:1500]}{_RERANKER_SUFFIX}"
            for d in docs
        ]
        inputs = tok(prompts, padding=True, truncation=True, return_tensors="pt", max_length=4096)
        if str(RERANKER_DEVICE) != "cpu":
            inputs = {k: v.to(RERANKER_DEVICE) for k, v in inputs.items()}  # 尊重配置 device(cuda / cuda:N)
        _t0 = time.perf_counter()
        with torch.no_grad():
            logits = model(**inputs).logits[:, -1, :]
            scores = torch.softmax(logits[:, [no_id, yes_id]], dim=-1)[:, 1].cpu().tolist()
        _record_stat("reranker", (time.perf_counter() - _t0) * 1000)
        return scores
    except Exception as exc:  # noqa: BLE001
        _record_stat_error("reranker", exc)
        _flog(f"[reranker] score FAIL: {type(exc).__name__}: {exc}")
        return None
