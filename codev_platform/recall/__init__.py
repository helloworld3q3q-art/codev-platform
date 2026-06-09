"""codev_platform.recall —— 跨 lane 检索融合 (Phase 6).

把分散的检索 lane(vector docs / bm25 / codegraph 符号 / graph 邻域 / memory)的产出
合成一路统一、可解释的排名。本包只承担**融合**这一职责; lane 取数(调 chroma/codegraph/
graph)与 query 分类选权重(复用 Phase 7 planner)在调用侧, 与本包解耦。
"""
from __future__ import annotations

from codev_platform.recall.fusion import (
    FusedHit,
    LaneResult,
    weighted_rrf,
)
from codev_platform.recall.service import CodeRecallHit, recall_code
from codev_platform.recall.weights import lane_weights_for

__all__ = ["CodeRecallHit", "FusedHit", "LaneResult", "lane_weights_for",
           "recall_code", "weighted_rrf"]
