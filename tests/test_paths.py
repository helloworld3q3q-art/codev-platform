"""Regression tests for codev_platform.core.paths.

chroma collections are namespaced as <pid>__<base>; codegraph DBs are per-project
isolated under codegraph_ext/<pid>/.
"""
from __future__ import annotations

import codev_platform.core.paths as paths
from codev_platform.core.repos import project_codegraph_dbs


def test_chroma_collection_name():
    assert paths.chroma_collection_name("openclaw-stock", "platform_docs") == \
        "openclaw-stock__platform_docs"


def test_chroma_collection_name_uses_separator():
    name = paths.chroma_collection_name("pid", "base")
    assert name == "pid" + paths.COLLECTION_SEP + "base"


def test_data_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    assert paths.data_root() == tmp_path.resolve()


def test_chroma_dir_under_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    assert paths.chroma_dir() == tmp_path.resolve() / "chroma"


def test_codegraph_db_path_per_project(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    a = paths.codegraph_db_path("p1")
    b = paths.codegraph_db_path("p2")
    assert a != b
    # 集中到平台后与 graph store 同父 (codegraph_ext/<pid>/codegraph/)
    assert a == tmp_path.resolve() / "codegraph_ext" / "p1" / "codegraph" / "codegraph.db"
    assert paths.codegraph_index_dir("p1") == tmp_path.resolve() / "codegraph_ext" / "p1" / "codegraph"


def test_project_codegraph_dbs_accepts_in_repo_db(tmp_path):
    repo = tmp_path / "repo"
    db = repo / ".codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"sqlite")

    got = project_codegraph_dbs("p1", cfg={"projects": {"p1": {"repo_path": str(repo)}}})

    assert [(spec.is_main, path) for spec, path in got] == [(True, db)]


def test_project_codegraph_dbs_central_fallback_respects_cfg_data_root(monkeypatch, tmp_path):
    cfg_data = tmp_path / "cfg-data"
    env_data = tmp_path / "env-data"
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(env_data))
    db = cfg_data / "codegraph_ext" / "p1" / "codegraph" / "codegraph.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"sqlite")

    got = project_codegraph_dbs("p1", cfg={"data": {"platform_data_dir": str(cfg_data)}})

    assert [(spec.is_main, path) for spec, path in got] == [(True, db)]
