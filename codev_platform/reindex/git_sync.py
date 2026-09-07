"""reindex 前把 repo 工作树追到远端 —— webhook 模型下服务器 clone 常落后于 push。

多机模型: 开发机 push → Gitea webhook → 服务器对该 repo reindex。若直接索引服务器
工作树, clone 还停在上一次状态, 索引的是旧代码。本模块在索引前补一次 `git pull
--ff-only`, 把工作树追到远端跟踪分支。

降级不阻断: 非 ff(分叉) / 无上游 / 无网络 / 超时 / 非 git 工作树, 一律不抛, 记 warn
返回 pulled=False + note 原因, reindex 照常用当前工作树继续。ff-only 本身安全 ——
不 merge / 不 --force / 不动用户未提交内容。
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from codev_platform.core.process_tree import run_tree

_PULL_TIMEOUT_SEC = 60
_DISCOVERY_TIMEOUT_SEC = 10
_NOTE_LIMIT = 200
_URL_USERINFO_RE = re.compile(r"(?P<scheme>https?://)[^@\s/]+@")
_TOKEN_PAIR_RE = re.compile(r"(?i)(token|password|passwd|secret|access_token)=([^&\s]+)")
_NON_INTERACTIVE_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "never",
    "SSH_ASKPASS_REQUIRE": "never",
}


def _redact_note(text: str) -> str:
    """git 输出入日志前脱敏: URL userinfo / query token 类片段。"""
    text = _URL_USERINFO_RE.sub(r"\g<scheme>***@", text)
    return _TOKEN_PAIR_RE.sub(r"\1=***", text)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_NON_INTERACTIVE_GIT_ENV)
    return env


def _git(repo: Path, *args: str, timeout: int) -> subprocess.CompletedProcess:
    return run_tree(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=_git_env(),
        no_window=True,
    )


def _clip_note(text: str, fallback: str) -> str:
    note = _redact_note(text.strip()) if text.strip() else fallback
    return note[:_NOTE_LIMIT] or fallback


def _tail_note(stdout: str, stderr: str, fallback: str) -> str:
    reason = stderr or stdout or fallback
    line = reason.strip().splitlines()[-1] if reason.strip() else fallback
    return _clip_note(line, fallback)


def _is_git_worktree(repo: Path, *, timeout: int) -> bool:
    cp = _git(repo, "rev-parse", "--is-inside-work-tree", timeout=timeout)
    return cp.returncode == 0 and (cp.stdout or "").strip().lower() == "true"


def repo_head(repo: Path, *, timeout_sec: int = _DISCOVERY_TIMEOUT_SEC) -> str | None:
    """当前本地 HEAD。失败/超时一律返回 None，供编排层做 fail-soft 记录。"""
    try:
        cp = _git(Path(repo), "rev-parse", "HEAD", timeout=timeout_sec)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if cp.returncode != 0:
        return None
    head = (cp.stdout or "").strip()
    return head or None


def repo_tree(repo: Path, *, timeout_sec: int = _DISCOVERY_TIMEOUT_SEC) -> str | None:
    """读取当前 HEAD 对应的 tree OID；失败或输出非法时返回 None。"""
    try:
        cp = _git(Path(repo), "rev-parse", "--verify", "HEAD^{tree}", timeout=timeout_sec)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if cp.returncode != 0:
        return None
    tree = (cp.stdout or "").strip()
    if (
        re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree) is None
        or set(tree) == {"0"}
    ):
        return None
    return tree


def repo_is_clean(repo: Path, *, timeout_sec: int = _DISCOVERY_TIMEOUT_SEC) -> bool:
    """工作树是否无 tracked/untracked 改动；失败或超时一律失败关闭。"""
    try:
        cp = _git(
            Path(repo),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--ignore-submodules=none",
            timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return cp.returncode == 0 and not (cp.stdout or "")


def repo_covers_commit(repo: Path, target_commit: str, *, timeout_sec: int = _DISCOVERY_TIMEOUT_SEC) -> bool:
    """本地 HEAD 是否已覆盖目标 commit。任何失败都按 False 处理，不阻塞调用方降级。"""
    try:
        cp = _git(Path(repo), "merge-base", "--is-ancestor", target_commit, "HEAD", timeout=timeout_sec)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return cp.returncode == 0


def sync_repo_to_remote(
    repo: Path,
    *,
    upstream_timeout_sec: int = _DISCOVERY_TIMEOUT_SEC,
    pull_timeout_sec: int = _PULL_TIMEOUT_SEC,
) -> dict:
    """索引前把 repo 追到远端 (git pull --ff-only)。降级安全, 永不抛。

    返回 {"pulled": bool, "note": str}:
      pulled=True  → 已与远端对齐 (含 already up-to-date)
      pulled=False → 跳过/失败 (note 给原因), 调用方照常用当前工作树继续
    """
    repo = Path(repo)

    try:
        if not _is_git_worktree(repo, timeout=upstream_timeout_sec):
            return {"pulled": False, "note": "not a git work tree"}
    except subprocess.TimeoutExpired:
        return {
            "pulled": False,
            "note": f"worktree check timed out after {upstream_timeout_sec}s",
        }
    except OSError as exc:
        return {
            "pulled": False,
            "note": _clip_note(f"worktree check failed: {exc!s}", "worktree check failed"),
        }

    try:
        upstream = _git(
            repo,
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
            timeout=upstream_timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {
            "pulled": False,
            "note": f"upstream check timed out after {upstream_timeout_sec}s",
        }
    except OSError as exc:
        return {
            "pulled": False,
            "note": _clip_note(f"upstream check failed: {exc!s}", "upstream check failed"),
        }
    if upstream.returncode != 0:
        return {"pulled": False, "note": "no upstream tracking branch"}

    try:
        # 服务仓同步属于 worker 内部物化步骤，不允许 post-merge 等用户钩子再次入队，
        # 否则同一提交会形成 pull -> hook -> enqueue 的反馈环。
        cp = _git(
            repo,
            "-c",
            "core.hooksPath=/dev/null",
            "pull",
            "--ff-only",
            timeout=pull_timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return {"pulled": False, "note": f"pull timed out after {pull_timeout_sec}s"}
    except OSError as exc:
        return {
            "pulled": False,
            "note": _clip_note(f"pull errored: {exc!s}", "pull errored"),
        }

    if cp.returncode == 0:
        note = _clip_note(cp.stdout or "up to date", "up to date")
        return {"pulled": True, "note": note}
    return {
        "pulled": False,
        "note": _tail_note(cp.stdout or "", cp.stderr or "", "pull failed"),
    }
