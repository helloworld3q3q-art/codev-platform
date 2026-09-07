"""正式 wheel 使用精确 Git 提交快照的测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from codev_platform.runtime_git_snapshot import (
    RuntimeGitSnapshotError,
    materialize_commit,
)
from tests.runtime_support import init_git_repo


def _commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_snapshot_is_bound_to_revision_not_live_worktree(tmp_path: Path) -> None:
    repo = init_git_repo(tmp_path / "repo")
    revision = _commit(repo)
    (repo / "README.md").write_text("mutated\n", encoding="utf-8")

    snapshot = materialize_commit(repo, revision, tmp_path / "snapshot")

    assert (snapshot / "README.md").read_text(encoding="utf-8") == "运行时测试\n"
    assert not (snapshot / ".git").exists()


def test_existing_destination_is_rejected(tmp_path: Path) -> None:
    repo = init_git_repo(tmp_path / "repo")
    destination = tmp_path / "snapshot"
    destination.mkdir()

    with pytest.raises(RuntimeGitSnapshotError, match="目标"):
        materialize_commit(repo, _commit(repo), destination)


def test_invalid_revision_is_rejected_before_git(tmp_path: Path) -> None:
    with pytest.raises(RuntimeGitSnapshotError, match="提交"):
        materialize_commit(tmp_path, "a" * 7, tmp_path / "snapshot")


def test_tracked_symlink_is_rejected(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows 测试环境不保证普通用户可创建符号链接")
    repo = init_git_repo(tmp_path / "repo")
    (repo / "link").symlink_to("tracked.txt")
    subprocess.run(["git", "add", "link"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "test: link"], cwd=repo, check=True)

    with pytest.raises(RuntimeGitSnapshotError, match="普通文件"):
        materialize_commit(repo, _commit(repo), tmp_path / "snapshot")
