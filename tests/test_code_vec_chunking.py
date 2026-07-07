"""code_vec 切割: kind 感知(类=头部摘要 / 方法=完整体+长则滑窗)+ 召回 sub-chunk 去重回节点。

替代旧的固定 1500 字符硬截断(6.1% 节点被切, 含 7492 方法切一半 / god-class 只嵌 0.2%)。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from codev_platform.recall.code_vector_store import (
    _BASE_FIELD_MAX,
    _CHUNK_BODY_CHARS,
    _DEFAULT_SKIP_KINDS,
    _collect_node_chunks,
    _existing_chroma_healthy,
    _node_chunks,
    _node_id_of,
    _head_at_line_boundary,
    _parse_query_result,
    _resolve_skip_kinds,
    _window_lines,
    build_text,
)


def _write(tmp_path: Path, rel: str, body: str) -> None:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_node_id_strips_suffix():
    assert _node_id_of("abc#3") == "abc"
    assert _node_id_of("abc") == "abc"


def test_build_text_caps_oversized_docstring_and_signature():
    # 实测 ideas-v2 有 docstring 达 17KB 的 Java 方法 → base 撑爆每个 chunk(慢 encode + 稀释)。
    node = {"name": "fetch", "qualifiedName": "p.C.fetch",
            "signature": "X" * 5000, "docstring": "Y" * 17000}   # X/Y 不出现在 name/qualifiedName
    t = build_text(node)
    assert t.count("X") == _BASE_FIELD_MAX["signature"]      # signature 截断到上界
    assert t.count("Y") == _BASE_FIELD_MAX["docstring"]      # docstring 截断到上界
    assert "fetch" in t and "p.C.fetch" in t                 # name/qualifiedName 不限, 保留
    # base 总长有界(防 24KB 巨型 chunk)
    assert len(t) < _BASE_FIELD_MAX["signature"] + _BASE_FIELD_MAX["docstring"] + 200


def test_build_text_short_fields_unchanged():
    node = {"name": "f", "qualifiedName": "p.C.f", "signature": "void f()", "docstring": "does x"}
    assert build_text(node) == "f\np.C.f\nvoid f()\ndoes x"


def test_head_at_line_boundary_never_cuts_midline():
    lines = ["aaaa", "bbbb", "cccc", "dddd"]
    head = _head_at_line_boundary(lines, budget=10)   # 'aaaa\nbbbb' = 9 < 10, +cccc 超
    assert head == "aaaa\nbbbb"
    # 单行超 budget 仍至少收 1 行(完整, 不空)
    assert _head_at_line_boundary(["x" * 50], budget=10) == "x" * 50


def test_window_lines_overlap_and_budget():
    lines = [f"line{i:02d}" for i in range(40)]   # 每行 6 字符
    wins = _window_lines(lines, budget=30, overlap=2)
    assert len(wins) > 1                                  # 切了多块
    for w in wins:
        assert len(w) <= 30 or "\n" not in w             # 每块 ≤ budget(单行除外)
    # 相邻窗有重叠(末行出现在下一窗开头附近)
    first_lines = wins[0].split("\n")
    second_lines = wins[1].split("\n")
    assert set(first_lines[-2:]) & set(second_lines), "相邻窗应重叠"


def test_container_kind_header_only_single_chunk(tmp_path):
    _write(tmp_path, "C.java", "\n".join(f"  int field{i};" for i in range(2000)))  # 巨型类体
    node = {"id": "cls1", "kind": "class", "name": "Big", "qualifiedName": "p.Big",
            "filePath": "C.java", "startLine": 1, "endLine": 2000}
    chunks = _node_chunks(node, tmp_path)
    assert len(chunks) == 1                       # 类只产 1 块(头部摘要)
    cid, text = chunks[0]
    assert cid == "cls1"                          # 裸 id(无 #)
    assert len(text) < 4000                       # 只头部, 不是整个 2000 行 body


def test_short_method_single_bare_chunk(tmp_path):
    _write(tmp_path, "S.java", "void f(){ return; }")
    node = {"id": "m1", "kind": "method", "name": "f", "qualifiedName": "p.C.f",
            "filePath": "S.java", "startLine": 1, "endLine": 1}
    chunks = _node_chunks(node, tmp_path)
    assert len(chunks) == 1 and chunks[0][0] == "m1"   # 短方法单块, 裸 id


def test_long_method_sliding_window_multi_chunk(tmp_path):
    body = "\n".join(f"    doStep{i}();" for i in range(800))   # 远超 _CHUNK_BODY_CHARS
    _write(tmp_path, "L.java", body)
    node = {"id": "m9", "kind": "method", "name": "huge", "qualifiedName": "p.C.huge",
            "filePath": "L.java", "startLine": 1, "endLine": 800}
    chunks = _node_chunks(node, tmp_path)
    assert len(chunks) > 1                              # 长方法切多块
    assert all(cid.startswith("m9#") for cid, _ in chunks)   # 都带 #k 后缀
    # 尾部逻辑(doStep799)出现在某块里 → 不丢尾
    assert any("doStep799" in t for _, t in chunks)
    # 每块带 base(方法名), 知道自己属于谁
    assert all("huge" in t for _, t in chunks)


def test_parse_query_result_dedups_subchunks_to_node():
    """同一节点的多个 sub-chunk 命中 → 只返一个节点(最高分那块), id 是裸 node id。"""
    res = {"ids": [["m9#2", "m9#0", "other"]],
           "metadatas": [[{"name": "huge", "kind": "method", "file": "L.java", "node": "m9"},
                          {"name": "huge", "kind": "method", "file": "L.java", "node": "m9"},
                          {"name": "x", "kind": "method", "file": "o.java", "node": "other"}]]}
    ranked, details = _parse_query_result(res)
    assert ranked == ["m9", "other"]                   # m9 去重, 保留首个(最高分)
    assert details["m9"]["name"] == "huge"


def test_parse_query_result_backward_compat_no_node_meta():
    """旧库 meta 无 'node' 字段 → 剥 '#k' 后缀回节点(向后兼容)。"""
    res = {"ids": [["a#0", "a#1", "b"]],
           "metadatas": [[{"name": "fa"}, {"name": "fa"}, {"name": "fb"}]]}
    ranked, _ = _parse_query_result(res)
    assert ranked == ["a", "b"]


# ---- skip_kinds 配置化(默认不变, config 可覆盖) ----

def test_resolve_skip_kinds_default_unchanged():
    """config 未设 → 默认 import/file/variable, 平台与现存项目行为零改动。"""
    assert _resolve_skip_kinds({}) == _DEFAULT_SKIP_KINDS
    assert _resolve_skip_kinds({"recall": {}}) == _DEFAULT_SKIP_KINDS


def test_resolve_skip_kinds_config_override():
    """config 设 list → 按 config(小写归一); Java 重仓可再排 field。"""
    cfg = {"recall": {"code_vec": {"skip_kinds": ["import", "File", "VARIABLE", "field"]}}}
    assert _resolve_skip_kinds(cfg) == frozenset({"import", "file", "variable", "field"})


def test_resolve_skip_kinds_empty_list_skips_nothing():
    """显式空 list = 不排除任何 kind = 全量嵌(区别于 None=默认)。"""
    cfg = {"recall": {"code_vec": {"skip_kinds": []}}}
    assert _resolve_skip_kinds(cfg) == frozenset()


def test_collect_node_chunks_localizes_extra_repo_refs(tmp_path, monkeypatch):
    """多仓 code_vec: 主仓 ref 不变, extra 仓 ref/file 加 tag, 同 node id 不碰撞。"""
    from codev_platform.core.repos import RepoSpec

    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    specs = [
        RepoSpec(root=main.resolve(), tag="", is_main=True, source_project_id="demo"),
        RepoSpec(root=extra.resolve(), tag="extra", is_main=False, source_project_id="extra-proj"),
    ]

    class FakeCodegraphClient:
        def __init__(self, *, db_path):
            self.db_path = Path(db_path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def iter_nodes(self):
            repo_name = self.db_path.parents[1].name
            return iter([
                {
                    "id": "same-id",
                    "kind": "function",
                    "name": f"fn_{repo_name}",
                    "filePath": "src/app.py",
                }
            ])

    monkeypatch.setattr(
        "codev_platform.web.integrations.codegraph_client.CodegraphClient",
        FakeCodegraphClient,
    )
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store._node_chunks",
        lambda node, repo: [(node["id"], f"text from {Path(repo).name}")],
    )

    manifest, text_by_id, meta_by_id = _collect_node_chunks(specs, frozenset())

    assert set(manifest) == {"same-id", "extra::same-id"}
    assert text_by_id["same-id"] == "text from main"
    assert text_by_id["extra::same-id"] == "text from extra"
    assert meta_by_id["same-id"]["node"] == "same-id"
    assert meta_by_id["same-id"]["file"] == "src/app.py"
    assert meta_by_id["extra::same-id"]["node"] == "extra::same-id"
    assert meta_by_id["extra::same-id"]["file"] == "extra::src/app.py"


def test_collect_node_chunks_skip_kinds_case_insensitive(tmp_path, monkeypatch):
    from codev_platform.core.repos import RepoSpec
    main = tmp_path / "main"; main.mkdir()

    class FakeCodegraphClient:
        def __init__(self, *, db_path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def iter_nodes(self):
            return iter([
                {"id": "file-id", "kind": "File", "name": "a", "filePath": "a.py"},
                {"id": "var-id", "kind": "VARIABLE", "name": "b", "filePath": "b.py"},
                {"id": "fn-id", "kind": "Function", "name": "c", "filePath": "c.py"},
            ])

    monkeypatch.setattr(
        "codev_platform.web.integrations.codegraph_client.CodegraphClient",
        FakeCodegraphClient,
    )
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store._node_chunks",
        lambda node, repo: [(node["id"], "text")],
    )

    manifest, _, _ = _collect_node_chunks(
        [RepoSpec(root=main.resolve(), is_main=True)],
        frozenset({"file", "variable"}),
    )

    assert set(manifest) == {"fn-id"}


def test_collect_node_chunks_skips_missing_extra_but_not_main(tmp_path, monkeypatch):
    from codev_platform.core.repos import RepoSpec
    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()

    class BoomCodegraphClient:
        def __init__(self, *, db_path):
            self.db_path = Path(db_path)

        def __enter__(self):
            if self.db_path.parents[1].name == "extra":
                raise RuntimeError("missing db")
            return self

        def __exit__(self, *exc):
            return None

        def iter_nodes(self):
            return iter([{"id": "main-id", "kind": "function", "filePath": "a.py"}])

    monkeypatch.setattr(
        "codev_platform.web.integrations.codegraph_client.CodegraphClient",
        BoomCodegraphClient,
    )
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store._node_chunks",
        lambda node, repo: [(node["id"], "text")],
    )

    manifest, _, _ = _collect_node_chunks([
        RepoSpec(root=main.resolve(), is_main=True),
        RepoSpec(root=extra.resolve(), tag="extra"),
    ], frozenset())
    assert set(manifest) == {"main-id"}

    with pytest.raises(RuntimeError):
        _collect_node_chunks([RepoSpec(root=extra.resolve(), is_main=True)], frozenset())


# ---- 增量续跑前探活(被中断写坏的库 → 退全量自愈) ----

def test_existing_chroma_healthy_missing_db_is_healthy(tmp_path):
    """首建无库文件 → 视为健康(交全量逻辑处理), 不误判。"""
    assert _existing_chroma_healthy(tmp_path) is True


def test_existing_chroma_healthy_valid_sqlite(tmp_path):
    """正常 sqlite 库 → quick_check ok → 健康, 允许增量续跑。"""
    import sqlite3
    db = tmp_path / "chroma.sqlite3"
    con = sqlite3.connect(db)
    con.execute("create table t(x int)")
    con.execute("insert into t values (1)")
    con.commit()
    con.close()
    assert _existing_chroma_healthy(tmp_path) is True


def test_existing_chroma_healthy_corrupt_file_detected(tmp_path):
    """被中断写坏的库(非法 sqlite 字节)→ 探活失败 → 调用方退全量 rmtree 自愈。"""
    db = tmp_path / "chroma.sqlite3"
    db.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)   # 伪头 + 垃圾 → malformed
    assert _existing_chroma_healthy(tmp_path) is False
