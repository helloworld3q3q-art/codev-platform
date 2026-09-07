"""运行时目录忽略契约测试。"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


_ROOT = Path(__file__).resolve().parents[1]


def test_gitignore同时覆盖venv目录与符号链接(tmp_path: Path) -> None:
    """不存在的同名路径按非目录匹配，可复现符号链接不匹配尾斜杠规则。"""
    git = shutil.which("git")
    if git is None:
        pytest.skip("当前环境没有 Git")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text(
        (_ROOT / ".gitignore").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run([git, "init", "-q"], cwd=repo, check=True)

    result = subprocess.run(
        [git, "check-ignore", "--no-index", ".venv"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, ".venv 符号链接必须被 Git 忽略"
