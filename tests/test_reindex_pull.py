"""sync_repo_to_remote 单测 —— 纯, monkeypatch run_tree, 不碰真 git / 网络。

覆盖: 真实 worktree 检测 / 非交互 Git 环境 / UTF-8 解码 / 脱敏 / 超时 / 降级安全。
所有失败路径必须降级 (不抛) 且 pulled=False。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from codev_platform.reindex import git_sync


class _CP:
    def __init__(self, rc: int, out: str = "", err: str = "") -> None:
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _mk_git_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _install_run_tree(monkeypatch, fake_run_tree):
    monkeypatch.setattr(git_sync, "run_tree", fake_run_tree, raising=False)


def _install_subprocess_run_stub(monkeypatch, steps):
    queue = list(steps)

    def fake_run(cmd, **kw):
        assert queue, f"unexpected subprocess.run call: {cmd!r}"
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    monkeypatch.setattr(subprocess, "run", fake_run)
    return queue


def test_git_worktree_detection_uses_rev_parse(tmp_path, monkeypatch):
    calls = []

    def fake_run_tree(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        return _CP(0, out="Already up to date.\n")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(monkeypatch, [])

    r = git_sync.sync_repo_to_remote(tmp_path)

    assert r["pulled"] is True
    assert calls[0][0][-2:] == ["rev-parse", "--is-inside-work-tree"]


def test_not_git_repo(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        return _CP(128, err="fatal: not a git repository")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(monkeypatch, [_CP(128, err="fatal: not a git repository")])

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "not a git" in r["note"]


def test_no_upstream(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        return _CP(128, err="no upstream configured")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [_CP(0, out="true\n"), _CP(128, err="no upstream configured")],
    )

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "no upstream" in r["note"]


def test_git_commands_use_non_interactive_env_and_utf8(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)
    calls = []

    def fake_run_tree(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        return _CP(0, out="Updating abc..def\nFast-forward\n")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [
            _CP(0, out="true\n"),
            _CP(0, out="origin/main\n"),
            _CP(0, out="Updating abc..def\nFast-forward\n"),
        ],
    )
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i existing-key")

    r = git_sync.sync_repo_to_remote(repo, upstream_timeout_sec=7, pull_timeout_sec=23)

    assert r["pulled"] is True
    assert len(calls) == 3
    assert [kw["timeout"] for _, kw in calls] == [7, 7, 23]
    assert calls[2][0][-4:] == [
        "-c",
        "core.hooksPath=/dev/null",
        "pull",
        "--ff-only",
    ]
    for _, kw in calls:
        assert kw["stdin"] is subprocess.DEVNULL
        assert kw["capture_output"] is True
        assert kw["text"] is True
        assert kw["encoding"] == "utf-8"
        assert kw["errors"] == "replace"
        assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kw["env"]["GCM_INTERACTIVE"] == "never"
        assert kw["env"]["SSH_ASKPASS_REQUIRE"] == "never"
        assert kw["env"]["GIT_SSH_COMMAND"] == "ssh -i existing-key"


def test_pull_non_ff(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        return _CP(128, err="fatal: Not possible to fast-forward, aborting.")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [
            _CP(0, out="true\n"),
            _CP(0, out="origin/main\n"),
            _CP(128, err="fatal: Not possible to fast-forward, aborting."),
        ],
    )

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "fast-forward" in r["note"].lower()


def test_pull_failure_redacts_url_credentials(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        return _CP(
            128,
            err=(
                "fatal: Authentication failed for "
                "'https://user:secret-token@git.local/repo.git?token=abc123'"
            ),
        )

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [
            _CP(0, out="true\n"),
            _CP(0, out="origin/main\n"),
            _CP(
                128,
                err=(
                    "fatal: Authentication failed for "
                    "'https://user:secret-token@git.local/repo.git?token=abc123'"
                ),
            ),
        ],
    )

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "secret-token" not in r["note"]
    assert "abc123" not in r["note"]
    assert "https://***@git.local" in r["note"]


def test_pull_timeout(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)
    timeout_exc = subprocess.TimeoutExpired(["git", "pull"], 23)
    calls = []

    def fake_run_tree(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        raise timeout_exc

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [_CP(0, out="true\n"), _CP(0, out="origin/main\n"), timeout_exc],
    )

    r = git_sync.sync_repo_to_remote(repo, pull_timeout_sec=23)

    assert r["pulled"] is False
    assert "timed out" in r["note"]
    assert len(calls) == 3


def test_pull_oserror(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        if cmd[-1] == "@{u}":
            return _CP(0, out="origin/main\n")
        raise OSError("git not found")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [_CP(0, out="true\n"), _CP(0, out="origin/main\n"), OSError("git not found")],
    )

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "errored" in r["note"]


def test_upstream_check_raises(tmp_path, monkeypatch):
    repo = _mk_git_repo(tmp_path)

    def fake_run_tree(cmd, **kw):
        if cmd[-2:] == ["rev-parse", "--is-inside-work-tree"]:
            return _CP(0, out="true\n")
        raise OSError("git missing")

    _install_run_tree(monkeypatch, fake_run_tree)
    _install_subprocess_run_stub(
        monkeypatch,
        [_CP(0, out="true\n"), OSError("git missing")],
    )

    r = git_sync.sync_repo_to_remote(repo)

    assert r["pulled"] is False
    assert "upstream check failed" in r["note"]


def test_repo_covers_commit_uses_merge_base(tmp_path, monkeypatch):
    calls = []

    def fake_run_tree(cmd, **kw):
        calls.append((cmd, kw))
        return _CP(0)

    _install_run_tree(monkeypatch, fake_run_tree)

    assert git_sync.repo_covers_commit(tmp_path, "deadbeef", timeout_sec=17) is True
    assert calls == [(
        ["git", "-C", str(tmp_path), "merge-base", "--is-ancestor", "deadbeef", "HEAD"],
        calls[0][1],
    )]
    assert calls[0][1]["timeout"] == 17


def test_repo_covers_commit_false_on_git_failure(tmp_path, monkeypatch):
    def fake_run_tree(cmd, **kw):
        return _CP(1, err="fatal: bad revision")

    _install_run_tree(monkeypatch, fake_run_tree)

    assert git_sync.repo_covers_commit(tmp_path, "deadbeef") is False


def test_repo_tree_reads_verified_head_tree(tmp_path, monkeypatch):
    calls = []
    tree = "a" * 40

    def fake_run_tree(cmd, **kw):
        calls.append((cmd, kw))
        return _CP(0, out=tree + "\n")

    _install_run_tree(monkeypatch, fake_run_tree)

    assert git_sync.repo_tree(tmp_path, timeout_sec=19) == tree
    assert calls[0][0] == [
        "git", "-C", str(tmp_path), "rev-parse", "--verify", "HEAD^{tree}",
    ]
    assert calls[0][1]["timeout"] == 19


def test_repo_tree_rejects_invalid_oid_or_git_failure(tmp_path, monkeypatch):
    steps = iter([_CP(0, out="not-an-oid\n"), _CP(1, err="bad tree")])
    _install_run_tree(monkeypatch, lambda *_args, **_kwargs: next(steps))

    assert git_sync.repo_tree(tmp_path) is None
    assert git_sync.repo_tree(tmp_path) is None


def test_repo_tree_timeout_is_bounded_failure(tmp_path, monkeypatch):
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["git"], 3)

    _install_run_tree(monkeypatch, timed_out)

    assert git_sync.repo_tree(tmp_path, timeout_sec=3) is None


def test_repo_tree_rejects_all_zero_oid(tmp_path, monkeypatch):
    _install_run_tree(monkeypatch, lambda *_args, **_kwargs: _CP(0, out="0" * 40 + "\n"))

    assert git_sync.repo_tree(tmp_path) is None


def test_repo_is_clean_uses_bounded_porcelain_status(tmp_path, monkeypatch):
    calls = []

    def fake_run_tree(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _CP(0, out="")

    _install_run_tree(monkeypatch, fake_run_tree)

    assert git_sync.repo_is_clean(tmp_path, timeout_sec=13) is True
    assert calls[0][0] == [
        "git", "-C", str(tmp_path), "status", "--porcelain=v1", "--untracked-files=all",
        "--ignore-submodules=none",
    ]
    assert calls[0][1]["timeout"] == 13


def test_repo_is_clean_rejects_dirty_failure_or_timeout(tmp_path, monkeypatch):
    steps = iter([
        _CP(0, out=" M changed.py\n"),
        _CP(1, err="not a repo"),
        subprocess.TimeoutExpired(["git"], 3),
    ])

    def fake_run_tree(*_args, **_kwargs):
        step = next(steps)
        if isinstance(step, BaseException):
            raise step
        return step

    _install_run_tree(monkeypatch, fake_run_tree)

    assert git_sync.repo_is_clean(tmp_path) is False
    assert git_sync.repo_is_clean(tmp_path) is False
    assert git_sync.repo_is_clean(tmp_path, timeout_sec=3) is False
