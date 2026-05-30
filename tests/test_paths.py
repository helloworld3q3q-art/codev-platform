"""Regression tests for codev_platform.core.paths.

Bug context: cross_link DB must be PER-PROJECT isolated (a <pid> subdir), with NO
legacy fallback that would make two projects share one cross_layer.sqlite. chroma
collections are namespaced as <pid>__<base>.
"""
from __future__ import annotations

from pathlib import Path

import codev_platform.core.paths as paths


def test_cross_link_db_path_has_pid_subdir(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    p = paths.cross_link_db_path("openclaw-stock")
    assert p == tmp_path / "codegraph_ext" / "openclaw-stock" / "cross_layer.sqlite"
    # pid appears as a path component (isolation), not just concatenated
    assert "openclaw-stock" in p.parts


def test_cross_link_db_path_distinct_per_project(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    a = paths.cross_link_db_path("openclaw-stock")
    b = paths.cross_link_db_path("codev-platform")
    # no legacy fallback sharing: different pid -> different DB file
    assert a != b
    assert a.parent != b.parent


def test_cross_link_db_path_not_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    per_project = paths.cross_link_db_path("openclaw-stock")
    legacy = paths.cross_link_legacy_db_path()
    # the per-project path must NOT collapse onto the legacy (pid-less) one
    assert per_project != legacy
    assert legacy == tmp_path / "codegraph_ext" / "cross_layer.sqlite"


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
    # 集中到平台后与 cross_layer.sqlite 同父 (codegraph_ext/<pid>/codegraph/)
    assert a == tmp_path.resolve() / "codegraph_ext" / "p1" / "codegraph" / "codegraph.db"
    assert paths.codegraph_index_dir("p1") == tmp_path.resolve() / "codegraph_ext" / "p1" / "codegraph"
