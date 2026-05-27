"""BM25 索引 — chroma 向量 RAG 的精确匹配伴侣。

每次 chroma collection 加载 / 重载时同步从 chroma 全量拉取构建 BM25,
不单独持久化(4255 chunks rebuild < 5s,可接受)。

设计目标:
- 中英混合分词(jieba + ASCII 正则)
- 保留代码符号 / 版本号 / 规则编号(BM25 命中精确符号比向量好)
- 兼容 chroma where 协议(category / module / $and)
- RRF (Reciprocal Rank Fusion) 融合向量 + BM25 两路召回

为什么不持久化:
- BM25 索引重建 < 5s,daemon 启动 / chroma 重建后自然触发
- 持久化引入"BM25 vs chroma 不同步"的隐性 bug 风险
- pickle 体积 30-50MB,启动反而慢
"""

from __future__ import annotations

import logging
import re
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)


# 混合 tokenizer 模式:
#   §0.13 / §3.2          规则编号
#   V202605270100 / N12   版本号 / 任务编号
#   snake_case / camel    英文标识符
#   一-龯                  中文连续段(后续 jieba 二次切)
_TOKEN_RE = re.compile(
    r"§\d+(?:\.\d+)*"
    r"|V?\d+[a-zA-Z0-9_]*"
    r"|[a-zA-Z][a-zA-Z0-9_]*"
    r"|[一-龥]+",
    re.UNICODE,
)
_CN_RE = re.compile(r"^[一-龥]+$")


def tokenize(text: str) -> list[str]:
    """中英混合 + 代码符号 + 版本号的 BM25 友好分词。

    - 英文 / 代码符号 / 数字 / 版本号:保留原 token,小写归一
    - 中文段:jieba.lcut_for_search(搜索引擎模式,粒度更细)
    - 跳过空白 / 标点
    """
    if not text:
        return []
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group()
        if _CN_RE.match(tok):
            out.extend(t for t in jieba.lcut_for_search(tok) if t.strip())
        else:
            out.append(tok.lower())
    return out


class BM25Index:
    """从 Chroma collection 全量构建的 BM25 倒排索引。"""

    def __init__(self) -> None:
        self._bm25: BM25Okapi | None = None
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict[str, Any]] = []
        self._id_to_idx: dict[str, int] = {}
        self._built = False

    def build(self, collection) -> int:
        """从 chroma collection 拉全量 + 构建索引。返回索引文档数(0 表示空集合)。"""
        all_data = collection.get(include=["documents", "metadatas"])
        ids = all_data.get("ids") or []
        docs = all_data.get("documents") or []
        metas = all_data.get("metadatas") or []
        if not ids:
            logger.warning("[bm25] empty collection, skip build")
            self._built = False
            return 0
        tokenized = [tokenize(doc or "") for doc in docs]
        self._bm25 = BM25Okapi(tokenized)
        self._ids = list(ids)
        self._docs = list(docs)
        self._metas = list(metas)
        self._id_to_idx = {cid: i for i, cid in enumerate(self._ids)}
        self._built = True
        logger.info(f"[bm25] built index: {len(ids)} chunks")
        return len(ids)

    def ready(self) -> bool:
        return self._built and self._bm25 is not None

    def get_doc(self, chunk_id: str) -> tuple[str, dict] | None:
        """按 chunk_id 取 (doc, meta),用于 RRF 后从 BM25 路径补 docs 给 reranker。"""
        idx = self._id_to_idx.get(chunk_id)
        if idx is None:
            return None
        return self._docs[idx], self._metas[idx]

    def search(
        self,
        query: str,
        n: int = 30,
        where: dict | None = None,
    ) -> list[tuple[str, float, str, dict]]:
        """BM25 检索。返回 [(chunk_id, score, doc, meta), ...] 按 score 降序,top n。

        where 过滤:支持 {"category": "rule"} / {"$and": [...]} chroma 协议子集。
        score = 0 的 chunk 不返回(BM25 0 分意味着无任何 token 命中)。
        """
        if not self.ready() or not query.strip():
            return []
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        scored: list[tuple[str, float, str, dict]] = []
        for idx, score in enumerate(scores):
            if score <= 0:
                continue
            meta = self._metas[idx] if idx < len(self._metas) else {}
            if not _match_where(meta, where):
                continue
            scored.append((self._ids[idx], float(score), self._docs[idx], meta))
        scored.sort(key=lambda x: -x[1])
        return scored[:n]


def _match_where(meta: dict, where: dict | None) -> bool:
    """兼容 chroma where 协议(单字段直传 / $and 多字段)。"""
    if not where:
        return True
    if "$and" in where:
        return all(_match_where(meta, c) for c in where["$and"])
    return all((meta or {}).get(k) == v for k, v in where.items())


def rrf_fuse(
    vector_ids: list[str],
    bm25_ids: list[str],
    k_const: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score = sum(1 / (k + rank))。

    vector_ids / bm25_ids:各自按分数降序的 chunk_id 列表
    返回:[(chunk_id, fused_score), ...] 按 fused_score 降序

    k_const=60 是 RRF 论文(Cormack et al. 2009)默认值,
    数值越大对低 rank 文档越宽容,数值越小越偏好 top rank。60 是工业实验最稳健值。
    """
    scores: dict[str, float] = {}
    for rank, cid in enumerate(vector_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k_const + rank + 1)
    for rank, cid in enumerate(bm25_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (k_const + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
