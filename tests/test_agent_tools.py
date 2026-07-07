"""agent 工具注册 + spec 形状测试(不触网 / 不查真 db)."""
from __future__ import annotations

import json
import sqlite3

from codev_platform.agent.tools import build_default_registry
from codev_platform.agent.tools.base import Tool, ToolRegistry


def test_default_registry_has_expected_tools():
    reg = build_default_registry()
    names = {t.name for t in reg.all()}
    # A 能力的三类 backend 都在 (cross_link 退役 -> impact 统一图谱工具)
    assert "table_usage" in names          # 统一图谱版, 取代旧 cross_link_table_refs
    assert "codegraph_search" in names
    assert "search_docs" in names
    # 退役工具不应再注册
    assert "cross_link_table_refs" not in names
    assert "cross_link_endpoint_callers" not in names


def test_specs_shape():
    reg = build_default_registry()
    for spec in reg.specs():
        assert set(spec) == {"name", "description", "input_schema"}
        assert spec["input_schema"]["type"] == "object"


def test_find_db_uses_centralized_platform_path(tmp_path, monkeypatch):
    # codegraph 数据 2026-05-30 起集中到平台: _find_db 应走
    # data_root/codegraph_ext/<pid>/codegraph/codegraph.db, 不依赖 meta.json repo_path
    # (跨机绝对路径会失效 —— WSL 跑的 agent 读到 Windows 'D:/...' 路径解析不到 → 误报未建索引)。
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    from codev_platform.agent.tools.codegraph import _find_db
    pid = "demo-proj"
    db = tmp_path / "codegraph_ext" / pid / "codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"")
    assert _find_db(pid) == db


def _seed_search_db(path, file_path, name="Target"):
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT, kind TEXT, file_path TEXT,
            start_line INTEGER, signature TEXT
        );
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT);
        CREATE VIRTUAL TABLE nodes_fts USING fts5(id, name);
        """
    )
    con.execute("INSERT INTO nodes VALUES ('n1', ?, 'function', ?, 7, 'def Target()')",
                (name, file_path))
    con.execute("INSERT INTO nodes_fts (id, name) VALUES ('n1', ?)", (name,))
    con.commit()
    con.close()


def _seed_relation_db(path, *, target="Target", other="Caller", incoming=True):
    path.parent.mkdir(parents=True)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT, kind TEXT, file_path TEXT,
            start_line INTEGER, signature TEXT
        );
        CREATE TABLE edges (source TEXT, target TEXT, kind TEXT);
        CREATE VIRTUAL TABLE nodes_fts USING fts5(id, name);
        """
    )
    con.execute("INSERT INTO nodes VALUES ('target-id', ?, 'function', 'src/target.py', 1, '')", (target,))
    con.execute("INSERT INTO nodes VALUES ('other-id', ?, 'function', 'src/other.py', 2, '')", (other,))
    if incoming:
        con.execute("INSERT INTO edges VALUES ('other-id', 'target-id', 'calls')")
    else:
        con.execute("INSERT INTO edges VALUES ('target-id', 'other-id', 'calls')")
    con.commit()
    con.close()


def _patch_two_repo_specs(monkeypatch, main, extra):
    from codev_platform.core.repos import RepoSpec
    specs = [
        RepoSpec(root=main, tag="", is_main=True, source_project_id="demo"),
        RepoSpec(root=extra, tag="extra", is_main=False, source_project_id="extra-demo"),
    ]
    monkeypatch.setattr("codev_platform.agent.tools.codegraph.project_repo_specs",
                        lambda pid: specs)
    return specs


def test_codegraph_search_fans_out_extra_repos(tmp_path, monkeypatch):
    from codev_platform.agent.tools.codegraph import CodegraphSearchTool

    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    main_db = main / ".codegraph" / "codegraph.db"
    extra_db = extra / ".codegraph" / "codegraph.db"
    _seed_search_db(main_db, "src/main.py")
    _seed_search_db(extra_db, "src/extra.py")
    _patch_two_repo_specs(monkeypatch, main, extra)

    res = CodegraphSearchTool("demo").run({"query": "Target"})

    assert not res.is_error, res.content
    locs = {item["loc"] for item in json.loads(res.content)}
    assert "src/main.py:7" in locs
    assert "extra::src/extra.py:7" in locs


def test_codegraph_callers_fans_out_extra_repos(tmp_path, monkeypatch):
    from codev_platform.agent.tools.codegraph import CodegraphCallersTool

    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    _seed_relation_db(main / ".codegraph" / "codegraph.db")
    _seed_relation_db(extra / ".codegraph" / "codegraph.db")
    _patch_two_repo_specs(monkeypatch, main, extra)

    res = CodegraphCallersTool("demo").run({"name": "Target"})

    assert not res.is_error, res.content
    locs = {item["loc"] for item in json.loads(res.content)}
    assert "src/other.py:2" in locs
    assert "extra::src/other.py:2" in locs


def test_codegraph_trace_fans_out_extra_repos(tmp_path, monkeypatch):
    from codev_platform.agent.tools.codegraph import CodegraphTraceTool

    main = tmp_path / "main"; main.mkdir()
    extra = tmp_path / "extra"; extra.mkdir()
    _seed_relation_db(main / ".codegraph" / "codegraph.db", incoming=False)
    _seed_relation_db(extra / ".codegraph" / "codegraph.db", incoming=False)
    _patch_two_repo_specs(monkeypatch, main, extra)

    res = CodegraphTraceTool("demo").run({"name": "Target", "direction": "callees", "depth": 1})

    assert not res.is_error, res.content
    locs = {item["loc"] for item in json.loads(res.content)["levels"]["hop1"]}
    assert "src/other.py:2" in locs
    assert "extra::src/other.py:2" in locs


def test_registry_rejects_nameless_tool():
    class Bad(Tool):
        name = ""
        def run(self, args):  # pragma: no cover
            raise NotImplementedError

    r = ToolRegistry()
    try:
        r.register(Bad())
    except ValueError:
        return
    raise AssertionError("应拒绝空 name 工具")
