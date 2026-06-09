"""检索 / 排序质量指标 —— 纯函数, 无 IO, 全可单测。

输入约定:
- retrieved: 检索系统返回的 id 列表 (按相关性降序, rank 0 = 最相关)。
- relevant:  ground-truth 相关 id 集合 (set / 可迭代)。
- id 是不透明字符串 (文档路径 / 符号名 / 表名均可), 指标只看是否命中, 不关心语义。

这些指标对标信息检索学界标准定义 (recall@k / hit@k / MRR / precision@k),
不依赖任何模型或后端, 因此检索 / 图谱 / 跨层链路评测都可复用同一套打分。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence


def _top_k(retrieved: Sequence[str], k: int) -> list[str]:
    """取前 k 个 (k<=0 视为空截断)。保留出现顺序。"""
    if k <= 0:
        return []
    return list(retrieved[:k])


def recall_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """recall@k = (前 k 命中的相关 id 数) / (相关 id 总数)。

    相关集合为空时返回 0.0 (无可召回目标, 约定不算满分)。
    """
    rel = set(relevant)
    if not rel:
        return 0.0
    topk = set(_top_k(retrieved, k))
    hit = len(topk & rel)
    return hit / len(rel)


def hit_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> bool:
    """前 k 个里是否至少命中一个相关 id (布尔)。"""
    rel = set(relevant)
    if not rel:
        return False
    return bool(set(_top_k(retrieved, k)) & rel)


def precision_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """precision@k = (前 k 命中的相关 id 数) / k。

    分母固定为 k (标准定义), 即使 retrieved 不足 k 个也按 k 算 ——
    召回不够也是一种 "不精确"。k<=0 返回 0.0。
    """
    if k <= 0:
        return 0.0
    rel = set(relevant)
    topk = set(_top_k(retrieved, k))
    hit = len(topk & rel)
    return hit / k


def mrr(retrieved: Sequence[str], relevant: Iterable[str]) -> float:
    """Mean Reciprocal Rank (单 query 即 reciprocal rank): 1 / (首个相关命中的 1-based 排名)。

    无命中返回 0.0。多 query 的 MRR = 各 query reciprocal rank 的均值 (见 aggregate_mrr)。
    """
    rel = set(relevant)
    if not rel:
        return 0.0
    for idx, rid in enumerate(retrieved):
        if rid in rel:
            return 1.0 / (idx + 1)
    return 0.0


def aggregate_mrr(per_query: Sequence[tuple[Sequence[str], Iterable[str]]]) -> float:
    """多 query 平均 MRR。per_query: [(retrieved, relevant), ...]。空列表返回 0.0。"""
    if not per_query:
        return 0.0
    return sum(mrr(r, rel) for r, rel in per_query) / len(per_query)


def ndcg_at_k(retrieved: Sequence[str], relevant: Iterable[str], k: int) -> float:
    """nDCG@k (二元相关性): DCG@k / IDCG@k。

    DCG = Σ rel_i / log2(rank + 1) (rank 1-based, 故位置 i → log2(i+2));
    IDCG = 理想排序(相关项全排最前)的 DCG。nDCG ∈ [0,1]。
    比 recall@k **更惩罚"相关项排得靠后"** —— 比较两套排序(如加权 vs 等权融合)时更敏感。
    相关集合为空 / k<=0 返回 0.0。
    """
    rel = set(relevant)
    if not rel or k <= 0:
        return 0.0
    topk = _top_k(retrieved, k)
    dcg = sum(1.0 / math.log2(i + 2) for i, rid in enumerate(topk) if rid in rel)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(min(len(rel), k)))
    return dcg / idcg if idcg else 0.0


def aggregate_ndcg(per_query: Sequence[tuple[Sequence[str], Iterable[str]]], k: int) -> float:
    """多 query 平均 nDCG@k。per_query: [(retrieved, relevant), ...]。空列表返回 0.0。"""
    if not per_query:
        return 0.0
    return sum(ndcg_at_k(r, rel, k) for r, rel in per_query) / len(per_query)


def accuracy(correct: int, total: int) -> float:
    """正确数 / 总数。total<=0 返回 0.0(无样本约定不算满分)。

    分类 / 裁决类任务用 (如 memory 冲突消解 "胜出条是否等于期望")。
    """
    if total <= 0:
        return 0.0
    return correct / total
