"""platform_docs 每项目独立 chroma 库隔离 (chromadb 1.5.9 多 collection compaction 损坏的治本)。

断言:
1. chroma_docs_dir(pid) 在 chroma_dir()/docs/<pid> 下, 各项目互不相同。
2. _get_client(pid) 按项目返回不同 client (不同 persist path), 各库只见自己的 collection
   —— 一个项目重建/写入永不进入另一项目的库 → 不触发跨 collection compaction。
3. indexer 写侧 PERSIST_DIR 走 per-project, 但 .reindex.lock 仍在根 (全局 GPU 串行化)。
"""
from __future__ import annotations


import pytest

from codev_platform.core.project_id import ProjectIdError


def test_docs_dir_per_project(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    from codev_platform.core import paths
    a = paths.chroma_docs_dir("proj-a")
    b = paths.chroma_docs_dir("proj-b")
    assert a != b
    assert a.parent == paths.chroma_dir() / "docs"
    assert a.name == "proj-a" and b.name == "proj-b"


def test_docs_dir_rejects_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    from codev_platform.core import paths
    with pytest.raises(ProjectIdError, match="格式非法"):
        paths.chroma_docs_dir("../escape")


def test_get_client_isolation(tmp_path, monkeypatch):
    """两项目库各开各的: 在 A 库建 collection, B 库 list 不到它 (物理隔离)。"""
    pytest.importorskip("chromadb")
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    from codev_platform.chroma import _models
    # 进程内 client 缓存清掉, 避免跨用例串扰
    _models._clients.clear()

    ca = _models._get_client("proj-a")
    cb = _models._get_client("proj-b")
    assert ca is not cb  # 不同库不同 client
    assert _models._get_client("proj-a") is ca  # 同项目缓存命中

    ca.get_or_create_collection("proj-a__platform_docs")
    # B 库是独立物理库, 看不到 A 的 collection
    b_names = {c.name for c in cb.list_collections()}
    assert "proj-a__platform_docs" not in b_names


def test_indexer_lock_global_db_per_project():
    """PERSIST_DIR per-project (docs/<pid>); reindex lock 在 chroma_dir() 根 (全局 GPU 串行化)。"""
    from codev_platform.chroma import indexer
    from codev_platform.core.paths import chroma_dir, chroma_docs_dir
    # 已导入的 indexer 在本仓 cwd 下解析 PROJECT_ID == 'codev-platform'
    assert indexer.PERSIST_DIR == chroma_docs_dir(indexer.PROJECT_ID)
    assert indexer.PERSIST_DIR.parent == chroma_dir() / "docs"
    assert indexer._REINDEX_LOCK_DIR == chroma_dir()
    assert indexer._REINDEX_LOCK_PATH.parent == chroma_dir()
