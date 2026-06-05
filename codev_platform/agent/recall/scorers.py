"""打分器实现 —— KeywordScorer(零依赖地板)/ VectorScorer(Embedder+向量库)。

每个是独立 adapter,实现 base.Scorer。加 BM25 = 新增 Bm25Scorer 一个类 + registry 一行,不动这里。
"""
from __future__ import annotations

from codev_platform.agent.memory_store import MemoryEntry
from codev_platform.agent.recall.base import RankCtx, Scorer, _rank_for_query


class KeywordScorer(Scorer):
    """子串命中排序(_rank_for_query 口径)。**零依赖地板**:不碰模型 / chromadb / jieba,永远能跑。"""
    name = "keyword"

    def rank(self, entries: list[MemoryEntry], query: str, ctx: RankCtx) -> list[str]:
        return [e.id for e in _rank_for_query(entries, query, task_id=ctx.task_id)]


class VectorScorer(Scorer):
    """语义向量排序:经 MemoryVectorIndex.query_ids(已按 org_id + 可见作用域过滤)。

    只返回落在当前候选池(entries)内的 id —— 向量库可能含已被冲突消解压掉 / 已失效的条,
    不能让它们绕过前置流程回到结果里。索引异常 → 返回空(由 service 退回其它档,见降级)。
    """
    name = "vector"

    def __init__(self, index) -> None:
        self._index = index

    def rank(self, entries: list[MemoryEntry], query: str, ctx: RankCtx) -> list[str]:
        pool = {e.id for e in entries}
        raw = self._index.query_ids(query, org_id=ctx.org_id, scopes=ctx.scopes, k=ctx.k)
        return [i for i in raw if i in pool]
