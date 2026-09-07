"""生产者目标提交解析契约测试。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from codev_platform.reindex.target_commit import (
    TargetCommitError,
    require_target_commit,
    resolve_project_head,
    resolve_repo_head,
)


def _建立_git_仓(repo: Path) -> str:
    """建立一个真实 Git 仓，返回其完整 HEAD。"""
    repo.mkdir()
    for command in (
        ["git", "init", "--quiet", str(repo)],
        ["git", "-C", str(repo), "config", "user.name", "测试用户"],
        ["git", "-C", str(repo), "config", "user.email", "test@example.invalid"],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("测试\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "测试提交"],
                   check=True, capture_output=True, text=True)
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.mark.parametrize("value", [None, "", "HEAD", "deadbeef", "A" * 40, "0" * 40])
def test_目标提交只接受完整小写非零_oid(value):
    with pytest.raises(TargetCommitError):
        require_target_commit(value)


def test_解析仓当前_head返回完整_oid(tmp_path):
    expected = _建立_git_仓(tmp_path / "repo")

    actual = resolve_repo_head(tmp_path / "repo")

    assert actual == expected


def test_解析非_git仓不回退到_head别名(tmp_path):
    repo = tmp_path / "not-a-repository"
    repo.mkdir()

    with pytest.raises(TargetCommitError):
        resolve_repo_head(repo)


def test_项目当前_head只取主仓(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    expected = _建立_git_仓(repo)
    cfg = {"projects": {"demo-proj": {"repo_path": str(repo)}}}
    monkeypatch.setattr("codev_platform.core.repos.load_config", lambda: cfg)
    monkeypatch.setattr("codev_platform.core.repos._read_meta", lambda _pid: {})

    assert resolve_project_head("demo-proj") == expected
