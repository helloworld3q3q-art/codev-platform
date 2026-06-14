"""core/index_handoff.py —— blue-green 原子索引切换纯核单测(Phase 1 阶段一)。

零服务/零索引依赖, 纯路径 + os.replace 逻辑, Windows 可跑。
"""
from __future__ import annotations

import json
import os

import pytest

from codev_platform.core import index_handoff as ih


# ----------------------------- 向后兼容 -----------------------------

def test_resolve_current_no_pointer_falls_back_to_base(tmp_path):
    """无 current.json(旧布局/首次)→ resolve 退回 base 本身(零迁移)。"""
    assert ih.resolve_current(tmp_path) == tmp_path
    assert ih.read_pointer(tmp_path) is None


def test_resolve_current_pointer_to_missing_dir_falls_back(tmp_path):
    """pointer 指向已删 build → 退回 base(fail-soft, 不返回不存在路径)。"""
    (tmp_path / ih._POINTER).write_text(json.dumps({"build": "ghost"}), encoding="utf-8")
    assert ih.resolve_current(tmp_path) == tmp_path


def test_read_pointer_corrupt_json_is_none(tmp_path):
    (tmp_path / ih._POINTER).write_text("{not json", encoding="utf-8")
    assert ih.read_pointer(tmp_path) is None


# ----------------------------- begin / commit -----------------------------

def test_begin_build_creates_side_dir_without_touching_current(tmp_path):
    d = ih.begin_build(tmp_path, "b1")
    assert d.is_dir()
    assert d == tmp_path / "builds" / "b1"
    # current 未被 begin 触碰 → 仍退回 base
    assert ih.read_pointer(tmp_path) is None
    assert ih.resolve_current(tmp_path) == tmp_path


def test_begin_build_same_id_retry_clears_old_content(tmp_path):
    d = ih.begin_build(tmp_path, "b1")
    (d / "stale.txt").write_text("x", encoding="utf-8")
    d2 = ih.begin_build(tmp_path, "b1")   # 同 id 重试
    assert d2 == d
    assert not (d2 / "stale.txt").exists()   # 旧内容被清


def test_commit_switches_current(tmp_path):
    bd = ih.begin_build(tmp_path, "b1")
    (bd / "data.bin").write_text("payload", encoding="utf-8")
    ih.commit_build(tmp_path, "b1")
    assert ih.read_pointer(tmp_path) == "b1"
    assert ih.resolve_current(tmp_path) == tmp_path / "builds" / "b1"
    assert (ih.resolve_current(tmp_path) / "data.bin").read_text(encoding="utf-8") == "payload"


def test_commit_nonexistent_build_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        ih.commit_build(tmp_path, "never-built")


def test_commit_leaves_no_tmp_residue(tmp_path):
    ih.begin_build(tmp_path, "b1")
    ih.commit_build(tmp_path, "b1")
    assert not (tmp_path / ih._TMP).exists()   # os.replace 消费了 tmp


# ----------------------------- 端到端 blue-green 切换 -----------------------------

def test_end_to_end_two_builds_reader_never_sees_half(tmp_path):
    # build A 并 commit
    a = ih.begin_build(tmp_path, "a")
    (a / "v").write_text("A", encoding="utf-8")
    ih.commit_build(tmp_path, "a")
    assert (ih.resolve_current(tmp_path) / "v").read_text(encoding="utf-8") == "A"

    # build B 期间, current 仍指 A(reader 读旧, 不撞半成品)
    b = ih.begin_build(tmp_path, "b")
    assert ih.resolve_current(tmp_path) == tmp_path / "builds" / "a"   # 仍 A
    (b / "v").write_text("B", encoding="utf-8")

    # commit B → 原子切到 B
    ih.commit_build(tmp_path, "b")
    assert (ih.resolve_current(tmp_path) / "v").read_text(encoding="utf-8") == "B"


# ----------------------------- GC -----------------------------

def _make_build(tmp_path, bid, mtime):
    d = ih.begin_build(tmp_path, bid)
    os.utime(d, (mtime, mtime))   # 显式设 mtime 控制新旧序(可测)
    return d


def test_gc_keeps_recent_and_never_deletes_current(tmp_path):
    _make_build(tmp_path, "old1", 1000)
    _make_build(tmp_path, "old2", 2000)
    _make_build(tmp_path, "mid", 3000)
    _make_build(tmp_path, "new", 4000)
    ih.commit_build(tmp_path, "old1")   # current = 最旧的 old1(落在 keep 窗口外)

    removed = ih.gc_builds(tmp_path, keep=2)

    # keep=2 保 new/mid; current=old1 即便最旧也必保 → 只 old2 该删
    assert set(removed) == {"old2"}
    assert (tmp_path / "builds" / "old1").is_dir()   # current 永存
    assert (tmp_path / "builds" / "new").is_dir()
    assert (tmp_path / "builds" / "mid").is_dir()
    assert not (tmp_path / "builds" / "old2").exists()


def test_gc_no_builds_dir_returns_empty(tmp_path):
    assert ih.gc_builds(tmp_path) == []


def test_gc_keep_zero_still_protects_current(tmp_path):
    _make_build(tmp_path, "a", 1000)
    _make_build(tmp_path, "b", 2000)
    ih.commit_build(tmp_path, "a")
    removed = ih.gc_builds(tmp_path, keep=0)
    assert removed == ["b"]                          # 非 current 全删
    assert (tmp_path / "builds" / "a").is_dir()      # current 仍保


# ----------------------------- 安全: 防路径穿越 -----------------------------

@pytest.mark.parametrize("bad", ["", "  ", "..", ".", "a/b", "a\\b", "../escape", "x\x00y"])
def test_begin_build_rejects_unsafe_id(tmp_path, bad):
    with pytest.raises(ValueError):
        ih.begin_build(tmp_path, bad)


@pytest.mark.parametrize("bad", ["..", "a/b", "../escape"])
def test_commit_rejects_unsafe_id(tmp_path, bad):
    with pytest.raises(ValueError):
        ih.commit_build(tmp_path, bad)
