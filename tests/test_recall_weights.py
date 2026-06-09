"""query 类型 → lane 权重 + recall_code 自动调权单测 (recall/weights.py)。"""
from __future__ import annotations

from codev_platform.recall import lane_weights_for, service
from codev_platform.recall.fusion import LaneResult
from codev_platform.recall.service import CODEGRAPH_LANE, GRAPH_LANE, recall_code

PID = "t-recall-w"


# ---- 分类 → 权重(复用 planner.classify_query)----

def test_symbol_query_prefers_codegraph():
    w = lane_weights_for("where is the definition signature")
    assert w[CODEGRAPH_LANE] > w[GRAPH_LANE]


def test_impact_query_prefers_graph():
    w = lane_weights_for("impact blast radius who uses")
    assert w[GRAPH_LANE] > w[CODEGRAPH_LANE]


def test_general_query_is_balanced():
    w = lane_weights_for("xyzqwerty zzz")
    assert w[GRAPH_LANE] == w[CODEGRAPH_LANE]


# ---- recall_code 自动调权: 排序随 query 类型翻转 ----

def _patch_two_lanes(monkeypatch):
    monkeypatch.setattr(service, "_graph_lane", lambda *a: (
        LaneResult(GRAPH_LANE, ["G"]), {"G": {"name": "g", "kind": "db_table", "file": None}}))
    monkeypatch.setattr(service, "_codegraph_lane", lambda *a: (
        LaneResult(CODEGRAPH_LANE, ["C"]), {"C": {"name": "c", "kind": "method", "file": "S.java"}}))


def test_recall_code_symbol_query_ranks_codegraph_first(monkeypatch):
    _patch_two_lanes(monkeypatch)
    hits = recall_code("where is definition signature", PID)   # SYMBOL → codegraph 2x
    assert hits[0].ref == "C"


def test_recall_code_impact_query_ranks_graph_first(monkeypatch):
    _patch_two_lanes(monkeypatch)
    hits = recall_code("impact who uses blast radius", PID)     # IMPACT → graph 2x
    assert hits[0].ref == "G"


def test_recall_code_explicit_weights_override_auto(monkeypatch):
    _patch_two_lanes(monkeypatch)
    # symbol query 本会偏 codegraph, 但显式权重强压 graph → 覆盖自动值。
    hits = recall_code("where is definition", PID, weights={GRAPH_LANE: 9.0, CODEGRAPH_LANE: 1.0})
    assert hits[0].ref == "G"
