"""query 类型 → lane 权重 + recall_code 自动调权单测 (recall/weights.py)。"""
from __future__ import annotations

from codev_platform.recall import lane_weights_for, service
from codev_platform.recall.fusion import LaneResult
from pathlib import Path

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


def test_codegraph_lane_fans_out_repos_and_localizes_refs(tmp_path, monkeypatch):
    from codev_platform.core.repos import RepoSpec

    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    specs = [
        RepoSpec(root=main.resolve(), tag="", is_main=True),
        RepoSpec(root=extra.resolve(), tag="extra", is_main=False),
    ]
    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs", lambda pid: specs)

    class FakeCodegraphClient:
        def __init__(self, *, db_path):
            self.db_path = Path(db_path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def search(self, query, languages, kinds, limit, *, match_mode="and"):
            repo_name = self.db_path.parents[1].name
            return [
                {
                    "id": "same",
                    "name": f"{repo_name}_same",
                    "kind": "function",
                    "filePath": "src/app.py",
                },
                {
                    "id": f"{repo_name}-second",
                    "name": f"{repo_name}_second",
                    "kind": "function",
                    "filePath": "src/second.py",
                },
            ]

    monkeypatch.setattr(
        "codev_platform.web.integrations.codegraph_client.CodegraphClient",
        FakeCodegraphClient,
    )

    lane, details = service._codegraph_lane(PID, "same", 10)

    assert lane is not None
    assert lane.ranked == ["same", "extra::same", "main-second", "extra::extra-second"]
    assert details["same"]["file"] == "src/app.py"
    assert details["extra::same"]["file"] == "extra::src/app.py"
