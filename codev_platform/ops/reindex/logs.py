"""reindex.logs -- per-repo reindex.log helpers + git output capture.

Pure-move split out of the original ops/reindex.py (no logic change).
"""
from __future__ import annotations

import subprocess
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
