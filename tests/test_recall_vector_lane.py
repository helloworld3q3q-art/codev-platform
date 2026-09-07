"""vector 语义 lane 单测 (recall/code_vector_store.py + service._vector_lane)。

纯解析(_parse_query_result)/ 文本拼接(build_text)脱 IO 测; lane 编排测 fail-soft + 测试降权 +
融合接线。不触嵌入模型 / chroma(全 monkeypatch), Windows 可跑。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from codev_platform.recall import service
from codev_platform.recall.code_vector_store import (
    _diff_manifest,
    _embed_text,
    _parse_query_result,
    _source_snippet,
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


def test_query_code_vectors_k_le_0_empty(monkeypatch):
    # 审计 edge: k<=0 chromadb 会抛 TypeError; query_code_vectors 应直接空返不走异常路径。
    from codev_platform.recall.code_vector_store import query_code_vectors
    # 不应触达 chromadb（k<=0 早返）→ 即使无索引也返 ([],{})
    assert query_code_vectors("any-pid", "q", 0) == ([], {})
    assert query_code_vectors("any-pid", "q", -3) == ([], {})


def test_query_code_vectors_no_collection_fast_skip(tmp_path, monkeypatch):
    # Phase 8 优化: 没建好 code_vec → fs 探活快速空返, 不 import chromadb / 不建 client(省 ~1s 冷启动)。
    # 关键: 即使 persist 残留空 chroma.sqlite3(0 collection, 实测 codev-platform 即此态), 只要无
    # .manifest.json(成功构建标记)就跳过 —— 不能只看 chroma.sqlite3 在不在(那正是首版漏的坑)。
    import codev_platform.recall.code_vector_store as cvs
    persist = tmp_path / "code_vec" / "demo"
    persist.mkdir(parents=True)
    (persist / "chroma.sqlite3").write_bytes(b"x")        # 空库残留, 但无 manifest
    monkeypatch.setattr(cvs, "_code_vec_persist_dir", lambda pid: persist)
    assert cvs.query_code_vectors("demo", "find user", 5) == ([], {})   # 走 fs-skip, 不碰 chromadb


def _run_query(monkeypatch, persist, collection, *, client_getter=None):
    from codev_platform.recall import code_vector_query

    monkeypatch.setattr(
        "codev_platform.agent.embed.registry.build_embedder",
        lambda _cfg: SimpleNamespace(encode=lambda _query: [1.0]),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    client = SimpleNamespace(get_collection=lambda _name: collection)
    return code_vector_query.query_code_vectors(
        "demo", "query", 5,
        persist_dir_resolver=lambda _pid: persist,
        client_getter=client_getter or (lambda _path: client),
        clients={},
    )


def test_query_code_vectors_accepts_legacy_manifest_without_meta(monkeypatch, tmp_path):
    (tmp_path / ".manifest.json").write_text("{}", encoding="utf-8")
    collection = SimpleNamespace(query=lambda **_kwargs: {
        "ids": [["node"]], "metadatas": [[{"name": "node"}]],
    })

    ranked, _details = _run_query(monkeypatch, tmp_path, collection)

    assert ranked == ["node"]


def test_query_code_vectors_discards_result_if_generation_changes(monkeypatch, tmp_path):
    from codev_platform.recall.code_vector_io import write_json_atomic

    manifest = tmp_path / ".manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def query(**_kwargs):
        manifest.unlink()
        write_json_atomic(manifest, {})
        return {"ids": [["partial"]], "metadatas": [[{}]]}

    assert _run_query(monkeypatch, tmp_path, SimpleNamespace(query=query)) == ([], {})


def test_query_code_vectors_keeps_old_snapshot_after_pointer_switch(monkeypatch, tmp_path):
    old = tmp_path / "builds" / "old"
    new = tmp_path / "builds" / "new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / ".manifest.json").write_text("{}", encoding="utf-8")
    (new / ".manifest.json").write_text("{}", encoding="utf-8")
    pointer = tmp_path / "current.json"
    pointer.write_text(json.dumps({"build": "old"}), encoding="utf-8")

    def query(**_kwargs):
        pointer.write_text(json.dumps({"build": "new"}), encoding="utf-8")
        return {"ids": [["old-node"]], "metadatas": [[{}]]}

    ranked, _details = _run_query(monkeypatch, tmp_path, SimpleNamespace(query=query))

    assert ranked == ["old-node"]


def test_query_code_vectors_query_failure_is_fail_soft(monkeypatch, tmp_path):
    (tmp_path / ".manifest.json").write_text("{}", encoding="utf-8")

    def query(**_kwargs):
        raise OSError("concurrent sqlite transition")

    assert _run_query(monkeypatch, tmp_path, SimpleNamespace(query=query)) == ([], {})


@pytest.mark.parametrize("stage", ["client", "collection"])
def test_query_code_vectors_open_failure_is_fail_soft(monkeypatch, tmp_path, stage):
    (tmp_path / ".manifest.json").write_text("{}", encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise OSError(f"{stage} transition")

    getter = fail if stage == "client" else (
        lambda _path: SimpleNamespace(get_collection=fail)
    )

    assert _run_query(
        monkeypatch, tmp_path, SimpleNamespace(query=lambda **_kwargs: {}),
        client_getter=getter,
    ) == ([], {})


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


# ---- 源码片段富化(补 codegraph 未抽取的 docstring 语义)----

def test_source_snippet_reads_line_range(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc\nd\n", encoding="utf-8")
    node = {"filePath": "m.py", "startLine": 2, "endLine": 3}
    assert _source_snippet(tmp_path, node) == "b\nc"


def test_source_snippet_degrades_fail_soft(tmp_path):
    assert _source_snippet(None, {"filePath": "x", "startLine": 1, "endLine": 2}) == ""
    assert _source_snippet(tmp_path, {"filePath": "missing.py", "startLine": 1, "endLine": 2}) == ""
    assert _source_snippet(tmp_path, {"name": "n"}) == ""   # 无行号


def test_embed_text_appends_source(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("def foo():\n    '''does the X thing'''\n", encoding="utf-8")
    node = {"name": "foo", "filePath": "m.py", "startLine": 1, "endLine": 2}
    t = _embed_text(node, tmp_path)
    assert "foo" in t and "does the X thing" in t          # 源码 docstring 进了嵌入文本
    assert _embed_text(node, None) == "foo"                # 无 repo 退基础


# ---- 路径穿越防御(审计 B1)----

def test_source_snippet_blocks_path_traversal(tmp_path):
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    secret = tmp_path / "SECRET.txt"
    secret.write_text("TOP SECRET\n" * 3, encoding="utf-8")
    # 相对 .. 穿越 → 拒读
    assert _source_snippet(repo, {"filePath": "../SECRET.txt", "startLine": 1, "endLine": 2}) == ""
    # 绝对路径穿越 → 拒读
    assert _source_snippet(repo, {"filePath": str(secret), "startLine": 1, "endLine": 2}) == ""
    # repo 内正常文件 → 照读
    (repo / "ok.py").write_text("line1\nline2\n", encoding="utf-8")
    assert _source_snippet(repo, {"filePath": "ok.py", "startLine": 1, "endLine": 2}) == "line1\nline2"


# ---- build 入口 pid 校验(审计 B3, rmtree 边界纵深)----

def test_build_rejects_malicious_project_id():
    from codev_platform.core.errors import PlatformError
    from codev_platform.core.project_id import ProjectIdError
    from codev_platform.recall.code_vector_store import build_code_vector_index
    import pytest
    with pytest.raises((ProjectIdError, PlatformError, ValueError)):
        build_code_vector_index("../../../etc")   # validate 在 embedder/rmtree 前就拦下


# ---- query client 缓存 ----

def test_get_query_client_caches_per_path(monkeypatch):
    import codev_platform.recall.code_vector_store as m
    m._QUERY_CLIENTS.clear()
    calls = []

    class _FakeChroma:
        def PersistentClient(self, path):
            calls.append(path)
            return object()
    monkeypatch.setitem(__import__("sys").modules, "chromadb", _FakeChroma())
    c1 = m._get_query_client("/p/a")
    c2 = m._get_query_client("/p/a")
    c3 = m._get_query_client("/p/b")
    assert c1 is c2 and c1 is not c3          # 同 path 单例, 不同 path 各一
    assert calls == ["/p/a", "/p/b"]          # 每 path 只建一次
    m._QUERY_CLIENTS.clear()


# ---- R3: 源码富化模式指纹(repo 翻转检测)----

def test_read_enrich_mode(tmp_path):
    import json
    from codev_platform.recall.code_vector_store import _read_enrich_mode
    meta = tmp_path / ".manifest.meta.json"
    assert _read_enrich_mode(meta) is None                # 缺 → None(未知, 触发全量)
    meta.write_text(json.dumps({"enrich": True}), encoding="utf-8")
    assert _read_enrich_mode(meta) is True
    meta.write_text(json.dumps({"enrich": False}), encoding="utf-8")
    assert _read_enrich_mode(meta) is False
    meta.write_text("{bad json", encoding="utf-8")
    assert _read_enrich_mode(meta) is None                # 坏 → None


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
