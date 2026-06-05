"""融合实现 —— RrfFusion(RRF 多路合一)。

RRF 自持(纯算法 ~6 行),不 import chroma/core 的 rrf(层级:recall 不依赖 chroma;且避开 rrf 在
chroma.bm25 / core.ranking 间迁移的耦合)。公式 `score = Σ 1/(k+rank+1)` 与 chroma.bm25.rrf_fuse 一致,
稳定降序(ties 保插入序)→ 与 B1 行为等价。单路退化为恒等直通。
"""
from __future__ import annotations

from codev_platform.agent.recall.base import Fusion


class RrfFusion(Fusion):
    name = "rrf"

    def __init__(self, k: int = 60) -> None:
        self._k = k

    def fuse(self, ranked_lists: list[list[str]]) -> list[str]:
        lists = [lst for lst in ranked_lists if lst]
        if not lists:
            return []
        if len(lists) == 1:
            return list(lists[0])
        scores: dict[str, float] = {}
        order: list[str] = []                         # 首见顺序 → 稳定排序的 tie-break 基准
        for lst in lists:
            for rank, cid in enumerate(lst):
                if cid not in scores:
                    order.append(cid)
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (self._k + rank + 1)
        order.sort(key=lambda i: -scores[i])          # 稳定:同分保首见序
        return order
