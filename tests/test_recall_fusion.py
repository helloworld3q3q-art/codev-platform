"""跨 lane 融合核心单测 (recall/fusion.py) —— 加权 RRF + boost + 可解释, 纯函数无 IO。"""
from __future__ import annotations

from codev_platform.recall import FusedHit, LaneResult, weighted_rrf


def _refs(hits: list[FusedHit]) -> list[str]:
    return [h.ref for h in hits]


def test_empty_lanes_returns_empty():
    assert weighted_rrf([]) == []
    assert weighted_rrf([LaneResult("a", [])]) == []


def test_single_lane_preserves_order():
    hits = weighted_rrf([LaneResult("vector_docs", ["a", "b", "c"])])
    assert _refs(hits) == ["a", "b", "c"]
    assert all(h.score > 0 for h in hits)
    assert hits[0].lanes == ["vector_docs"]


def test_two_lanes_overlap_ranks_higher():
    # x 在两 lane 都 top → 累加得分高于只在一路命中的 y/z。
    lanes = [LaneResult("vector", ["x", "y"]), LaneResult("graph", ["x", "z"])]
    hits = weighted_rrf(lanes)
    assert _refs(hits)[0] == "x"
    assert set(hits[0].lanes) == {"vector", "graph"}  # 命中两 lane, 可解释


def test_weighting_lets_heavier_lane_dominate():
    # graph 权重远高 → graph 独占的 g 压过 vector 独占的 v(即便同 rank)。
    lanes = [LaneResult("vector", ["v"]), LaneResult("graph", ["g"])]
    hits = weighted_rrf(lanes, weights={"graph": 5.0, "vector": 1.0})
    assert _refs(hits)[0] == "g"


def test_boost_lifts_recalled_ref():
    lanes = [LaneResult("vector", ["a", "b"])]
    # b 本在 a 之后, 给 b 大 boost → 反超。
    hits = weighted_rrf(lanes, boosts={"b": 1.0})
    assert _refs(hits)[0] == "b"
    assert hits[0].boost == 1.0


def test_boost_only_ref_not_invented():
    # 只在 boost_map、未被任何 lane 召回的 ref 不凭空入选(boost 只提升已召回项)。
    hits = weighted_rrf([LaneResult("vector", ["a"])], boosts={"ghost": 99.0})
    assert "ghost" not in _refs(hits)


def test_top_k_per_lane_truncates_before_fusion():
    lanes = [LaneResult("vector", ["a", "b", "c", "d"])]
    hits = weighted_rrf(lanes, top_k_per_lane=2)
    assert _refs(hits) == ["a", "b"]


def test_limit_caps_results():
    hits = weighted_rrf([LaneResult("v", ["a", "b", "c"])], limit=2)
    assert len(hits) == 2 and _refs(hits) == ["a", "b"]


def test_stable_tie_break_first_seen_order():
    # 两 ref 各只在一个等权 lane top0 → 同分 → 保首见序(x 先于 y)。
    lanes = [LaneResult("a", ["x"]), LaneResult("b", ["y"])]
    hits = weighted_rrf(lanes)
    assert _refs(hits) == ["x", "y"]
    assert hits[0].score == hits[1].score


def test_lanes_sorted_by_weight_in_explanation():
    # 命中多 lane 时, FusedHit.lanes 按权重降序(强 lane 在前, 解释更可读)。
    lanes = [LaneResult("weak", ["x"]), LaneResult("strong", ["x"])]
    hits = weighted_rrf(lanes, weights={"strong": 9.0, "weak": 1.0})
    assert hits[0].lanes == ["strong", "weak"]
