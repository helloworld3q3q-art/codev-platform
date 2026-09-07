"""reindex.logs -- per-repo reindex.log helpers + git output capture.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path


# ----------------------------------------------------------------------
# shared: per-repo reindex.log location (mirrors post-commit.ps1)
# ----------------------------------------------------------------------
def _reindex_log(repo: Path) -> Path:
    return repo / "tools" / "chroma" / "reindex.log"


def _append_log(path: Path, text: str) -> None:
    """Append UTF-8 (no BOM) -- mirrors the .NET AppendAllText in post-commit.ps1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_epoch() -> str:
    """Return a timezone-independent hook timestamp for machine comparisons."""
    return f"{time.time():.6f}"


def _git_out(repo: Path | None, *git_args: str) -> tuple[int, str]:
    cmd = ["git"]
    if repo is not None:
        cmd += ["-C", str(repo)]
    cmd += list(git_args)
    try:
        # Windows 下 text=True 不指定 encoding 会按 GBK 解码 git 输出,撞中文 commit/diff
        # 内容 → UnicodeDecodeError 崩 reader 线程 → 静默跳过 reindex(2026-06-02 修)
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, ""
    return cp.returncode, cp.stdout.strip()


def commit_changed_paths(
    repo: Path,
    commit: str,
    *,
    git_out: Callable[..., tuple[int, str]] = _git_out,
) -> tuple[int, list[str]]:
    """返回 commit 相对第一父的去重路径；root commit 自动回退。"""
    rc, raw = git_out(
        repo, "diff-tree", "--no-renames", "--no-commit-id", "--name-only",
        "-r", f"{commit}^1", commit,
    )
    paths = _changed_paths(raw) if rc == 0 else []
    if rc != 0:
        rc, raw = git_out(
            repo, "diff-tree", "--root", "--no-renames", "--no-commit-id",
            "--name-only", "-r", commit,
        )
        paths = _changed_paths(raw) if rc == 0 else []
    return rc, paths


def _changed_paths(raw: str) -> list[str]:
    return list(dict.fromkeys(line.strip() for line in raw.splitlines() if line.strip()))


def changed_paths_between(
    repo: Path,
    before: str,
    after: str,
    *,
    git_out: Callable[..., tuple[int, str]] = _git_out,
) -> tuple[int, list[str]]:
    """返回两版本间 rename 两端的路径，不受用户 diff.renames 配置影响。"""
    rc, raw = git_out(repo, "diff", "--no-renames", "--name-only", before, after)
    return rc, _changed_paths(raw) if rc == 0 else []


def integration_changed_paths(
    repo: Path,
    commit: str,
    *,
    git_out: Callable[..., tuple[int, str]] = _git_out,
) -> tuple[int, list[str]] | None:
    """HEAD 最近一次为 merge/pull 时，返回完整 ORIG_HEAD..HEAD 范围。"""
    rc, head = git_out(repo, "rev-parse", "HEAD")
    if rc != 0 or head.strip() != commit:
        return None
    rc, subject = git_out(repo, "reflog", "-1", "--format=%gs", "HEAD")
    action = subject.strip().lower()
    if rc != 0 or not action.startswith(("merge ", "merge:", "pull ", "pull:")):
        return None
    rc, base = git_out(repo, "rev-parse", "--verify", "ORIG_HEAD")
    if rc != 0 or not base.strip():
        return None
    rc, _ = git_out(repo, "merge-base", "--is-ancestor", base.strip(), commit)
    if rc != 0:
        return None
    return changed_paths_between(repo, base.strip(), commit, git_out=git_out)
