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


class Bm25Scorer(Scorer):
    """BM25 关键词排序(jieba 中英混合分词 + rank_bm25,纯 CPU,无 GPU)。

    对候选池(已是几十~几百条小集合)现算 BM25,比 KeywordScorer 的子串命中强(词频/分词)。
    复用 `chroma.bm25.tokenize`(单源中英分词)。依赖(rank_bm25/jieba)缺失由 registry 工厂剔除降级。
    """
    name = "bm25"

    def rank(self, entries: list[MemoryEntry], query: str, ctx: RankCtx) -> list[str]:
        if not entries:
            return []
        from rank_bm25 import BM25Okapi  # lazy:仅 bm25 档启用时才付依赖

        from codev_platform.chroma.bm25 import tokenize
        corpus = [tokenize(e.content) for e in entries]
        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(tokenize(query))
        # 稳定降序:同分保候选池原序(recency 兜底)。BM25 在小语料有两种"全 0"退化 —— query 词
        # 全不命中、或命中超半数(IDF≤0 被 BM25Okapi epsilon 钳 0);两者都退化成纯池序,靠 RRF
        # 与 vector/keyword 融合补偿(故 bm25 一般不单用)。
        ranked = sorted(zip(entries, scores, strict=True), key=lambda pair: -pair[1])
        return [e.id for e, _ in ranked]


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
