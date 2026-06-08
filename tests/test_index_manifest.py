"""统一索引 manifest (Phase 1) 纯单测 —— 用临时 db 路径, 不碰真 data_root。"""
from __future__ import annotations

import argparse

from codev_platform import index_manifest as im
from codev_platform.index_manifest import BuildRecord


def _db(tmp_path):
    return tmp_path / "m.sqlite"


# ----------------------------------------------------------- record / read

def test_record_and_read_roundtrip(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord(
        "p1", "chroma", git_commit="abc123", started_at=1.0, finished_at=3.5,
        status="ok", chunk_count=42), path=db)
    recs = im.read_manifest(path=db)
    assert len(recs) == 1
    r = recs[0]
    assert (r.project_id, r.kind, r.status) == ("p1", "chroma", "ok")
    assert r.git_commit == "abc123" and r.chunk_count == 42
    assert r.elapsed_sec == 2.5


def test_replace_keeps_latest(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "graph", git_commit="old", status="ok"), path=db)
    im.record_build(BuildRecord("p1", "graph", git_commit="new", status="failed"), path=db)
    recs = im.read_manifest("p1", path=db)
    assert len(recs) == 1  # 同 (project,kind) 只留最新一次
    assert recs[0].git_commit == "new" and recs[0].status == "failed"


def test_read_missing_db_returns_empty(tmp_path):
    assert im.read_manifest(path=tmp_path / "nope.sqlite") == []


def test_filter_by_project(tmp_path):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "chroma"), path=db)
    im.record_build(BuildRecord("p2", "chroma"), path=db)
    assert {r.project_id for r in im.read_manifest("p1", path=db)} == {"p1"}
    assert len(im.read_manifest(path=db)) == 2


def test_elapsed_none_when_missing_times():
    assert BuildRecord("p", "k").elapsed_sec is None


# ----------------------------------------------------------- freshness

def test_freshness_fresh_stale_unknown(tmp_path, monkeypatch):
    db = _db(tmp_path)
    im.record_build(BuildRecord("p1", "chroma", git_commit="aaa", status="ok",
                                finished_at=1.0), path=db)
    im.record_build(BuildRecord("p1", "graph", git_commit="bbb", status="ok"), path=db)
    # 当前 HEAD == aaa -> chroma 对齐, graph 落后
    monkeypatch.setattr(im, "git_head", lambda repo: "aaa")
    f = {d["kind"]: d for d in im.freshness("p1", "/fake/repo", path=db)}
    assert f["chroma"]["fresh"] is True and f["chroma"]["reason"] == "对齐 HEAD"
    assert f["graph"]["fresh"] is False and "落后" in f["graph"]["reason"]
    # 无 repo -> 无法判定
    f2 = {d["kind"]: d for d in im.freshness("p1", None, path=db)}
    assert f2["chroma"]["fresh"] is None


# ----------------------------------------------------------- CLI 注册

def test_index_command_registered():
    from codev_platform import ops
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    ops.register_all(sub)
    assert "index" in sub.choices, "ops.register_all 应注册 index 子命令"
