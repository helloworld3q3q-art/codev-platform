"""跨 lane 代码召回服务单测 (recall/service.py)。

纯融合+富化(_fuse_and_enrich)脱 IO 测; recall_code 编排测 fail-soft + 真实 graph store 集成。
"""
from __future__ import annotations

from codev_platform.graph.schema import AnalyzerResult, GraphNode, NodeKind
from codev_platform.core.repos import RepoSpec
from codev_platform.graph.store import open_store as real_open_store
from codev_platform.recall import service
from codev_platform.recall.fusion import LaneResult
from codev_platform.recall.service import _fuse_and_enrich, recall_code

PID = "t-recall-svc"


# ---- 纯融合 + 富化 ----

def test_fuse_and_enrich_merges_and_explains():
    lanes = [LaneResult("graph", ["g1", "g2"]), LaneResult("codegraph", ["g1", "c1"])]
    details = {
        "g1": {"name": "save_user", "kind": "backend_function", "file": "repo.py"},
        "g2": {"name": "users", "kind": "db_table", "file": None},
        "c1": {"name": "saveUser", "kind": "method", "file": "Svc.java"},
    }
    hits = _fuse_and_enrich(lanes, details)
    # g1 命中两 lane → 得分叠加排第一, 富化 + 来源可解释
    assert hits[0].ref == "g1"
    assert set(hits[0].lanes) == {"graph", "codegraph"}
    assert hits[0].name == "save_user" and hits[0].kind == "backend_function"
    assert hits[0].file == "repo.py"


def test_fuse_and_enrich_missing_detail_falls_back_to_ref():
    hits = _fuse_and_enrich([LaneResult("graph", ["x"])], {})
    assert hits[0].name == "x" and hits[0].kind == ""


# ---- recall_code 编排 ----

def test_recall_code_empty_query_returns_empty():
    assert recall_code("   ", PID) == []


def test_recall_code_both_lanes_empty(monkeypatch):
    monkeypatch.setattr(service, "_graph_lane", lambda *a: (None, {}))
    monkeypatch.setattr(service, "_codegraph_lane", lambda *a: (None, {}))
    assert recall_code("anything", PID) == []


def test_recall_code_fail_soft_one_lane(monkeypatch):
    # graph lane 出结果, codegraph lane 挂 → 仍返回 graph 结果(高可用)。
    monkeypatch.setattr(service, "_graph_lane", lambda *a: (
        LaneResult("graph", ["g1"]), {"g1": {"name": "foo", "kind": "fn", "file": "a.py"}}))
    monkeypatch.setattr(service, "_codegraph_lane", lambda *a: (None, {}))
    hits = recall_code("foo", PID)
    assert len(hits) == 1 and hits[0].name == "foo" and hits[0].lanes == ["graph"]


def test_recall_code_graph_integration(monkeypatch, tmp_path):
    # 真实 graph store: search_nodes 子串命中; codegraph db 不存在 → fail-soft → graph-only。
    store = tmp_path / "g.sqlite"
    c = real_open_store(PID, path=store)
    c.upsert_result(PID, AnalyzerResult(
        nodes=[GraphNode(id="fn:save_user", kind=NodeKind.BACKEND_FUNCTION.value,
                         name="save_user", project_id=PID, file="repo.py"),
               GraphNode(id="t:users", kind=NodeKind.DB_TABLE.value,
                         name="orders", project_id=PID)],
        plugin="test"))
    c.close()
    monkeypatch.setattr("codev_platform.graph.store.open_store",
                        lambda pid, mode="rw": real_open_store(pid, path=store, mode=mode))
    hits = recall_code("user", PID)
    assert len(hits) == 1                      # 只 save_user 含 "user"
    assert hits[0].name == "save_user" and hits[0].lanes == ["graph"]


# ---- 测试文件降权 ----

def test_is_test_hit_detects_tests_and_impls():
    from codev_platform.recall.service import _is_test_hit
    assert _is_test_hit("test_stamp_provenance", "tests/test_graph_provenance.py") is True
    assert _is_test_hit("foo", "web-ui/src/x.spec.ts") is True
    assert _is_test_hit("foo", "a/b_test.py") is True
    assert _is_test_hit("stamp_provenance", "codev_platform/graph/schema.py") is False
    assert _is_test_hit("save_user", "repo.py") is False


def test_deprioritize_tests_keeps_impl_first():
    from codev_platform.recall.service import _deprioritize_tests
    items = [
        ("t1", "test_stamp_provenance_a", "tests/test_p.py"),   # 测试(原序靠前)
        ("impl", "stamp_provenance", "codev_platform/graph/schema.py"),  # 真实现
        ("t2", "test_stamp_provenance_b", "tests/test_p.py"),
    ]
    assert _deprioritize_tests(items) == ["impl", "t1", "t2"]   # 实现顶到最前, 测试组内保原序


def test_codegraph_lane_main_repo_failure_continues_extra_repo(monkeypatch, tmp_path):
    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    main_spec = RepoSpec(main, tag="", is_main=True)
    extra_spec = RepoSpec(extra, tag="extra", is_main=False)

    class FakeCodegraphClient:
        def __init__(self, *, db_path=None, project_id=None):
            self.db_path = db_path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def search(self, query, kind, path, limit, *, match_mode=None):
            if self.db_path == main_spec.codegraph_db:
                raise RuntimeError("main codegraph missing")
            return [{
                "id": "n-extra",
                "name": "run_extra",
                "kind": "function",
                "filePath": "src/extra.py",
            }]

    monkeypatch.setattr("codev_platform.core.repos.project_repo_specs",
                        lambda project_id: [main_spec, extra_spec])
    monkeypatch.setattr("codev_platform.web.integrations.codegraph_client.CodegraphClient",
                        FakeCodegraphClient)

    lane, details = service._codegraph_lane(PID, "run", 10)

    assert lane is not None
    assert lane.lane == service.CODEGRAPH_LANE
    assert lane.ranked == ["extra::n-extra"]
    assert details["extra::n-extra"] == {
        "name": "run_extra",
        "kind": "function",
        "file": "extra::src/extra.py",
    }
