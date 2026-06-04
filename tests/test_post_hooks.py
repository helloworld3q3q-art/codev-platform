"""post-merge / post-checkout hook 逻辑(双实例 plan P0:本地"获取新代码"也更新索引)。

纯逻辑可单测:scope 归类 + post-checkout 跳过非切分支。spawn/git 部分留给集成。
"""
from __future__ import annotations

from types import SimpleNamespace

from codev_platform.ops import reindex


def test_classify_scopes_buckets():
    pats = {"doc": [r"\.md$"], "codegraph": [r"\.py$", r"\.java$"]}
    changed = ["a.md", "b.py", "d.txt", "e.java"]
    scoped = reindex.classify_scopes(changed, pats)
    assert scoped["chroma"] == ["a.md"]
    assert sorted(scoped["codegraph"]) == ["b.py", "e.java"]
    assert "d.txt" not in str(scoped)          # 无 scope 命中的不进


def test_classify_scopes_empty_when_no_match():
    pats = {"doc": [r"\.md$"], "codegraph": []}
    assert reindex.classify_scopes(["x.txt", "y.png"], pats) == {}


def test_post_checkout_skips_file_checkout():
    # flag != "1"(单文件 checkout, 非切分支)→ 直接 0, 不碰 git/reindex
    args = SimpleNamespace(prev="a", new="b", flag="0", foreground=True)
    assert reindex.cmd_post_checkout(args) == 0


def test_post_checkout_skips_same_head():
    args = SimpleNamespace(prev="abc", new="abc", flag="1", foreground=True)
    assert reindex.cmd_post_checkout(args) == 0


def test_parser_registers_new_hooks():
    from codev_platform.cli import build_parser
    p = build_parser()
    # 三个 hook 子命令都能解析(不抛 SystemExit)
    for argv in (["post-commit"], ["post-merge", "0"], ["post-checkout", "aaa", "bbb", "1"]):
        ns = p.parse_args(argv)
        assert callable(ns.func)
