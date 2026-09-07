"""记忆召回服务 —— back-compat 薄 shim(2026-06-05 解构)。

真身在 `codev_platform/agent/recall/` 包(base/scorers/fusion/reranker/service/registry);本文件只
re-export 老符号,保 `from codev_platform.agent.recall_service import ...` 的存量导入 / 测试不破。

老两类现为 PipelineRecallService 的薄 shim(零逻辑重复,行为等价):
  LocalRecallService  = scorers[KeywordScorer]
  VectorRecallService = scorers[VectorScorer, KeywordScorer] + RrfFusion
设计:`docs/plans/roadmap-2026-06-05/memory-recall-pluggable-pipeline-2026-06-05.md`。
"""
from __future__ import annotations

from codev_platform.agent.memory_recall import DEFAULT_POLICY
from codev_platform.agent.memory_store import MemoryStore
from codev_platform.agent.recall.base import (  # noqa: F401 — re-export
    RankCtx, RecallService, _rank_for_query, _score, visible_scopes,
)
from codev_platform.agent.recall.fusion import RrfFusion
from codev_platform.agent.recall.reranker import NoReranker
from codev_platform.agent.recall.scorers import KeywordScorer, VectorScorer
from codev_platform.agent.recall.service import PipelineRecallService

__all__ = [
    "RecallService", "PipelineRecallService", "LocalRecallService", "VectorRecallService",
    "visible_scopes", "_score", "_rank_for_query", "RankCtx",
]


class LocalRecallService(PipelineRecallService):
    """关键词召回(零依赖地板)。= PipelineRecallService(scorers=[KeywordScorer])。"""

    def __init__(self, store: MemoryStore, *, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50, rbac_store=None) -> None:
        super().__init__(store, scorers=[KeywordScorer()], fusion=RrfFusion(), reranker=NoReranker(),
                         default_policy=default_policy, per_scope_limit=per_scope_limit,
                         rbac_store=rbac_store)


class VectorRecallService(PipelineRecallService):
    """语义召回。= PipelineRecallService(scorers=[VectorScorer, KeywordScorer], RrfFusion)。"""

    def __init__(self, store: MemoryStore, index, *, default_policy: str = DEFAULT_POLICY,
                 per_scope_limit: int = 50, rbac_store=None, rrf_k: int = 60) -> None:
        super().__init__(store, scorers=[VectorScorer(index), KeywordScorer()],
                         fusion=RrfFusion(k=rrf_k), reranker=NoReranker(),
                         default_policy=default_policy, per_scope_limit=per_scope_limit,
                         rbac_store=rbac_store)
