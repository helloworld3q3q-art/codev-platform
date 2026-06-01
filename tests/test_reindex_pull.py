"""sync_repo_to_remote 单测 —— 纯, monkeypatch subprocess, 不碰真 git / 网络。

覆盖: pull 成功 / 非 ff (rc!=0) / 抛异常 / 超时 / 无上游 / 非 git 工作树。
所有失败路径必须降级 (不抛) 且 pulled=False。
"""
from __future__ import annotations

import subprocess

import pytest

from codev_platform.reindex import git_sync


class _CP:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _mk_git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_not_git_repo(tmp_path):
    r = git_sync.sync_repo_to_remote(tmp_path)  # no .git
    assert r["pulled"] is False
    assert "not a git" in r["note"]


def test_no_upstream(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        # rev-parse @{u} -> non-zero (no upstream)
        return _CP(128, err="no upstream configured")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is False
    assert "no upstream" in r["note"]


def test_pull_success(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        if "@{u}" in cmd:
            return _CP(0, out="origin/main")
        return _CP(0, out="Updating abc..def\nFast-forward")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is True
    assert "Fast-forward" in r["note"]


def test_pull_non_ff(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        if "@{u}" in cmd:
            return _CP(0, out="origin/main")
        return _CP(128, err="fatal: Not possible to fast-forward, aborting.")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is False
    assert "fast-forward" in r["note"].lower()


def test_pull_timeout(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        if "@{u}" in cmd:
            return _CP(0, out="origin/main")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 60))

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is False
    assert "timed out" in r["note"]


def test_pull_oserror(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        if "@{u}" in cmd:
            return _CP(0, out="origin/main")
        raise OSError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is False
    assert "errored" in r["note"]


def test_upstream_check_raises(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run(cmd, **kw):
        raise OSError("git missing")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = git_sync.sync_repo_to_remote(repo)
    assert r["pulled"] is False
    assert "upstream check failed" in r["note"]
