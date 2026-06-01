"""reindex 前把 repo 工作树追到远端 —— webhook 模型下服务器 clone 常落后于 push。

多机模型: 开发机 push → Gitea webhook → 服务器对该 repo reindex。若直接索引服务器
工作树, clone 还停在上一次状态, 索引的是旧代码。本模块在索引前补一次 `git pull
--ff-only`, 把工作树追到远端跟踪分支。

降级不阻断: 非 ff(分叉) / 无上游 / 无网络 / 超时 / 非 git 工作树, 一律不抛, 记 warn
返回 pulled=False + note 原因, reindex 照常用当前工作树继续。ff-only 本身安全 ——
不 merge / 不 --force / 不动用户未提交内容。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_PULL_TIMEOUT_SEC = 60


def _git(repo: Path, *args: str, timeout: int = _PULL_TIMEOUT_SEC) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def sync_repo_to_remote(repo: Path) -> dict:
    """索引前把 repo 追到远端 (git pull --ff-only)。降级安全, 永不抛。

    返回 {"pulled": bool, "note": str}:
      pulled=True  → 已与远端对齐 (含 already up-to-date)
      pulled=False → 跳过/失败 (note 给原因), 调用方照常用当前工作树继续
    """
    repo = Path(repo)
    if not (repo / ".git").exists():
        return {"pulled": False, "note": "not a git work tree (.git missing)"}

    # 有无上游跟踪分支 (无上游 pull 会报错, 提前判定降级)
    try:
        upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
                        timeout=10)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"pulled": False, "note": f"upstream check failed: {exc!s}"}
    if upstream.returncode != 0:
        return {"pulled": False, "note": "no upstream tracking branch"}

    try:
        cp = _git(repo, "pull", "--ff-only")
    except subprocess.TimeoutExpired:
        return {"pulled": False, "note": f"pull timed out after {_PULL_TIMEOUT_SEC}s"}
    except OSError as exc:  # git 不在 PATH 等
        return {"pulled": False, "note": f"pull errored: {exc!s}"}

    if cp.returncode == 0:
        return {"pulled": True, "note": (cp.stdout or "up to date").strip()[:200] or "up to date"}
    # 非 ff (分叉) / 无网络 / 其它: 降级, 不阻断
    reason = (cp.stderr or cp.stdout or "non-fast-forward or network error").strip()
    return {"pulled": False, "note": reason.splitlines()[-1][:200] if reason else "pull failed"}
