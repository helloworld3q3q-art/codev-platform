"""召回装配 registry —— config 驱动选 scorers/fusion/reranker,零 if-else;依赖缺失自动降级。

加一个 scorer/fusion/reranker = `register_*` 一行 + adapter 一个类,不动 service / build。
降级(原则 #5):某档依赖缺(向量库 None / 模型未装 / jieba 缺)→ 跳过该档;scorers 剔空 → 强制补
keyword 地板;**保证永远能装出可跑的 pipeline**。
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from codev_platform.agent.recall.base import Fusion, Reranker, Scorer
from codev_platform.agent.recall.fusion import RrfFusion
from codev_platform.agent.recall.reranker import NoReranker, QwenReranker
from codev_platform.agent.recall.scorers import Bm25Scorer, KeywordScorer, VectorScorer
from codev_platform.agent.recall.service import PipelineRecallService

_log = logging.getLogger(__name__)

# scorer 工厂:名 → (index) -> Scorer | None(None = 依赖不满足,降级跳过)
_SCORERS: dict[str, Callable[..., Scorer | None]] = {}
_FUSIONS: dict[str, Callable[..., Fusion]] = {}
_RERANKERS: dict[str, Callable[..., Reranker]] = {}

# recall_backend 旧值 → scorers 列表(back-compat 别名)
_BACKEND_ALIAS = {"local": ["keyword"], "vector": ["vector", "keyword"]}


def register_scorer(name: str, factory: Callable[..., Scorer | None]) -> None:
    _SCORERS[name] = factory


def register_fusion(name: str, factory: Callable[..., Fusion]) -> None:
    _FUSIONS[name] = factory


def register_reranker(name: str, factory: Callable[..., Reranker]) -> None:
    _RERANKERS[name] = factory


# ---- 内置注册(keyword/vector/bm25 + rrf + none)----
register_scorer("keyword", lambda index=None, **_: KeywordScorer())
register_scorer("vector", lambda index=None, **_: VectorScorer(index) if index is not None else None)
register_scorer("bm25", lambda index=None, **_: Bm25Scorer() if _bm25_available() else None)
register_fusion("rrf", lambda rrf_k=60, **_: RrfFusion(k=rrf_k))
register_reranker("none", lambda cfg=None, **_: NoReranker())


def _build_qwen_reranker(cfg=None, **_):
    """qwen 精排:rerank 模型(默认 remote 复用 chroma daemon)不可用 → 退 NoReranker(降级)。"""
    from codev_platform.agent.embed.registry import build_rerank_model
    model = build_rerank_model(cfg or {})
    if model is None:
        _log.warning("[recall] rerank=qwen 但 rerank 模型不可用,退 NoReranker")
        return NoReranker()
    return QwenReranker(model)


register_reranker("qwen", _build_qwen_reranker)


def _bm25_available() -> bool:
    """rank_bm25 + jieba 都在才启 bm25 档;缺则工厂返 None → 降级跳过(原则 #5)。"""
    import importlib.util
    return (importlib.util.find_spec("rank_bm25") is not None
            and importlib.util.find_spec("jieba") is not None)


def _resolve_scorer_names(cfg_get, cfg) -> list[str]:
    """config 决定 scorers:显式 memory.recall.scorers > recall_backend 别名 > 默认 ['keyword']。"""
    names = cfg_get(cfg, "memory.recall.scorers")
    if isinstance(names, list) and names:
        return [str(n) for n in names]
    backend = cfg_get(cfg, "memory.recall_backend", "local")
    return _BACKEND_ALIAS.get(backend, ["keyword"])


def build_recall_service(cfg, store, *, index=None, rbac_store=None):
    """从 config 装配 PipelineRecallService(降级保底:剔不可用档,最低退 keyword)。store=None → None。"""
    if store is None:
        return None
    from codev_platform.core.config import get as cfg_get

    rrf_k = cfg_get(cfg, "memory.recall.rrf_k", 60)
    policy = cfg_get(cfg, "memory.conflict_policy", "personal_first")
    rerank_top_k = cfg_get(cfg, "memory.recall.rerank_top_k", 20)

    # scorers:按名建,缺依赖(工厂返 None)/ 未知名 → 跳过;剔空 → 补 keyword 地板
    scorers: list[Scorer] = []
    for name in _resolve_scorer_names(cfg_get, cfg):
        factory = _SCORERS.get(name)
        if factory is None:
            _log.warning("[recall] 未知 scorer %r,跳过", name)
            continue
        s = factory(index=index)
        if s is None:
            _log.warning("[recall] scorer %r 依赖不满足(降级跳过)", name)
            continue
        scorers.append(s)
    if not scorers:
        _log.warning("[recall] 无可用 scorer,降级到 keyword 地板")
        scorers = [KeywordScorer()]

    fusion = _FUSIONS.get(cfg_get(cfg, "memory.recall.fusion", "rrf"), _FUSIONS["rrf"])(rrf_k=rrf_k)
    reranker = _RERANKERS.get(cfg_get(cfg, "memory.recall.rerank", "none"), _RERANKERS["none"])(cfg)

    return PipelineRecallService(
        store, scorers=scorers, fusion=fusion, reranker=reranker,
        default_policy=policy, rbac_store=rbac_store, rerank_top_k=rerank_top_k,
    )
