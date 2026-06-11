"""vector 语义 lane 单测 (recall/code_vector_store.py + service._vector_lane)。

纯解析(_parse_query_result)/ 文本拼接(build_text)脱 IO 测; lane 编排测 fail-soft + 测试降权 +
融合接线。不触嵌入模型 / chroma(全 monkeypatch), Windows 可跑。
"""
from __future__ import annotations

from codev_platform.recall import service
from codev_platform.recall.code_vector_store import (
    _diff_manifest,
    _parse_query_result,
    build_text,
)
from codev_platform.recall.fusion import LaneResult
from codev_platform.recall.service import VECTOR_LANE, recall_code

PID = "t-recall-vec"


# ---- 纯解析 ----

def test_parse_query_result_maps_ids_and_details():
    res = {"ids": [["n1", "n2"]],
           "metadatas": [[{"name": "save_user", "kind": "function", "file": "repo.py"},
                          {"name": "load", "kind": "method", "file": "svc.py"}]]}
    ranked, details = _parse_query_result(res)
    assert ranked == ["n1", "n2"]
    assert details["n1"] == {"name": "save_user", "kind": "function", "file": "repo.py"}
    assert details["n2"]["file"] == "svc.py"


def test_parse_query_result_empty():
    assert _parse_query_result({}) == ([], {})
    assert _parse_query_result({"ids": [[]]}) == ([], {})


def test_parse_query_result_missing_meta_falls_back_to_none():
    ranked, details = _parse_query_result({"ids": [["x"]], "metadatas": [[None]]})
    assert ranked == ["x"]
    assert details["x"] == {"name": None, "kind": None, "file": None}


# ---- 嵌入文本拼接 ----

def test_build_text_joins_meaningful_fields():
    node = {"name": "save_user", "qualifiedName": "repo.save_user",
            "signature": "def save_user(u)", "docstring": "persist a user",
            "filePath": "repo.py"}  # filePath 不入嵌入文本
    t = build_text(node)
    assert "save_user" in t and "persist a user" in t and "def save_user(u)" in t
    assert "repo.py" not in t


def test_build_text_skips_empty_fields():
    assert build_text({"name": "x"}).strip() == "x"
    assert build_text({}) == ""


# ---- 增量 diff (manifest) ----

def test_diff_manifest_detects_changed_new_deleted():
    old = {"a": "h1", "b": "h2", "gone": "h9"}
    new = {"a": "h1", "b": "h2new", "c": "h3"}   # a 不变, b 变, c 新增, gone 删
    changed, deleted = _diff_manifest(old, new)
    assert set(changed) == {"b", "c"}            # 不变的 a 不重嵌
    assert deleted == ["gone"]


def test_diff_manifest_empty_old_is_full():
    changed, deleted = _diff_manifest({}, {"a": "h1", "b": "h2"})
    assert set(changed) == {"a", "b"} and deleted == []


# ---- lane 编排 (fail-soft / 降权) ----

def test_vector_lane_fail_soft_when_store_missing(monkeypatch):
    # collection/依赖不可用 → query_code_vectors 抛 → lane (None, {})。
    def _boom(*a, **k):
        raise RuntimeError("no collection")
    monkeypatch.setattr("codev_platform.recall.code_vector_store.query_code_vectors", _boom)
    assert service._vector_lane(PID, "anything", 10) == (None, {})


def test_vector_lane_empty_result_returns_none(monkeypatch):
    monkeypatch.setattr("codev_platform.recall.code_vector_store.query_code_vectors",
                        lambda *a, **k: ([], {}))
    assert service._vector_lane(PID, "q", 10) == (None, {})


def test_vector_lane_deprioritizes_tests(monkeypatch):
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.query_code_vectors",
        lambda *a, **k: (["t1", "impl"], {
            "t1": {"name": "test_save", "kind": "function", "file": "tests/test_x.py"},
            "impl": {"name": "save", "kind": "function", "file": "repo.py"}}))
    lane, details = service._vector_lane(PID, "save", 10)
    assert lane.lane == VECTOR_LANE
    assert lane.ranked == ["impl", "t1"]   # 实现顶到前, 测试靠后


# ---- 融合接线: vector 单 lane 出结果也能召回 ----

def test_recall_code_includes_vector_lane(monkeypatch):
    monkeypatch.setattr(service, "_graph_lane", lambda *a: (None, {}))
    monkeypatch.setattr(service, "_codegraph_lane", lambda *a: (None, {}))
    monkeypatch.setattr(service, "_vector_lane", lambda *a: (
        LaneResult(VECTOR_LANE, ["v1"]),
        {"v1": {"name": "download_then_write", "kind": "function", "file": "p.py"}}))
    hits = recall_code("logic that writes after downloading data", PID)
    assert len(hits) == 1 and hits[0].lanes == [VECTOR_LANE]
    assert hits[0].name == "download_then_write"


def test_recall_code_vector_reinforces_codegraph_same_ref(monkeypatch):
    # 同一 node id 被 codegraph + vector 双命中 → 叠分排第一(ref 同空间的价值)。
    monkeypatch.setattr(service, "_graph_lane", lambda *a: (None, {}))
    monkeypatch.setattr(service, "_codegraph_lane", lambda *a: (
        LaneResult(service.CODEGRAPH_LANE, ["shared", "only_cg"]),
        {"shared": {"name": "f", "kind": "function", "file": "a.py"},
         "only_cg": {"name": "g", "kind": "function", "file": "b.py"}}))
    monkeypatch.setattr(service, "_vector_lane", lambda *a: (
        LaneResult(VECTOR_LANE, ["shared"]),
        {"shared": {"name": "f", "kind": "function", "file": "a.py"}}))
    hits = recall_code("xyzqwerty general", PID)   # GENERAL → 均衡两 lane (vector 加分但 ref 同)
    assert hits[0].ref == "shared"
    assert set(hits[0].lanes) == {service.CODEGRAPH_LANE, VECTOR_LANE}
