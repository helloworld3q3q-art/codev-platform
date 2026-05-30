"""codegraph 索引集中到平台(junction)—— 状态判定 + link/unlink 机制。

在 tmp 目录上建真 .codegraph + 真 junction/symlink, 验证移动 + 联接 + 透明读 + 幂等 + 回退。
"""
from __future__ import annotations

import os

import pytest

from codev_platform.ops import codegraph as cg
from codev_platform.core.paths import codegraph_index_dir


@pytest.fixture
def env(tmp_path, monkeypatch):
    """PLATFORM_DATA_DIR 指向 tmp, 造一个带 .codegraph 的假业务仓。"""
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "platdata"))
    repo = tmp_path / "repo"
    repo.mkdir()
    cgdir = repo / ".codegraph"
    cgdir.mkdir()
    (cgdir / "codegraph.db").write_text("INDEX-DATA", encoding="utf-8")
    return {"cfg": {}, "pid": "p1", "repo": repo, "cgdir": cgdir}


def test_link_state_in_repo_then_missing(env):
    repo_cg = env["repo"] / ".codegraph"
    plat = codegraph_index_dir("p1")
    assert cg.link_state(repo_cg, plat) == "in-repo"
    # 删掉 → missing
    import shutil
    shutil.rmtree(repo_cg)
    assert cg.link_state(repo_cg, plat) == "missing"


def test_link_moves_to_platform_and_junctions(env):
    plat = codegraph_index_dir("p1")
    r = cg.link_project(env["cfg"], "p1", env["repo"])
    assert r["action"] == "moved+linked"
    # 物理数据在平台
    assert (plat / "codegraph.db").read_text(encoding="utf-8") == "INDEX-DATA"
    # 仓内 .codegraph 成了联接
    repo_cg = env["repo"] / ".codegraph"
    assert cg._is_link(repo_cg)
    # 经联接透明读到平台数据(工具无感)
    assert (repo_cg / "codegraph.db").read_text(encoding="utf-8") == "INDEX-DATA"
    # 状态 = linked
    assert cg.link_state(repo_cg, plat) == "linked"


def test_link_idempotent(env):
    cg.link_project(env["cfg"], "p1", env["repo"])
    r2 = cg.link_project(env["cfg"], "p1", env["repo"])
    assert r2["action"] == "already-linked"


def test_unlink_moves_back(env):
    cg.link_project(env["cfg"], "p1", env["repo"])
    r = cg.unlink_project(env["cfg"], "p1", env["repo"])
    assert r["action"] == "unlinked+moved-back"
    repo_cg = env["repo"] / ".codegraph"
    assert not cg._is_link(repo_cg)
    assert repo_cg.is_dir()
    assert (repo_cg / "codegraph.db").read_text(encoding="utf-8") == "INDEX-DATA"
    assert not codegraph_index_dir("p1").exists()


def test_link_platform_only_relinks(env):
    # 模拟"数据已在平台但仓内无联接"(重 clone 后): 先 link, 删联接, 再 link → relinked
    cg.link_project(env["cfg"], "p1", env["repo"])
    repo_cg = env["repo"] / ".codegraph"
    cg._remove_link(repo_cg)
    assert cg.link_state(repo_cg, codegraph_index_dir("p1")) == "platform-only"
    r = cg.link_project(env["cfg"], "p1", env["repo"])
    assert r["action"] == "relinked"
    assert (repo_cg / "codegraph.db").read_text(encoding="utf-8") == "INDEX-DATA"


def test_link_dry_run_does_not_move(env):
    r = cg.link_project(env["cfg"], "p1", env["repo"], dry_run=True)
    assert "would-link" in r["action"]
    assert (env["repo"] / ".codegraph").is_dir()       # 没动
    assert not codegraph_index_dir("p1").exists()
