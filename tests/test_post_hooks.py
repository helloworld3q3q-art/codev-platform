"""post-merge / post-checkout hook 逻辑(双实例 plan P0:本地"获取新代码"也更新索引)。

纯逻辑可单测:scope 归类 + post-checkout 跳过非切分支。spawn/git 部分留给集成。
"""
from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

from codev_platform.ops import hooks, reindex
from codev_platform.ops.hooks import cmd_install_hooks
from codev_platform.ops.reindex import commands as reindex_commands
from codev_platform.reindex.producer_route import LocalHookRelayRequired
from codev_platform.ops.reindex.logs import (
    changed_paths_between,
    commit_changed_paths,
    integration_changed_paths,
)


_TARGET_COMMIT = "a" * 40


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


def test_expected_reindex_kinds_matches_hook_expansion():
    pats = {"doc": [r"\.md$"], "codegraph": [r"\.py$"]}
    assert reindex.expected_reindex_kinds(["docs/a.md"], pats) == ["chroma"]
    assert reindex.expected_reindex_kinds(["src/app.py"], pats) == [
        "codegraph",
        "ingest",
        "code_vec",
    ]


def test_post_checkout_skips_file_checkout():
    # flag != "1"(单文件 checkout, 非切分支)→ 直接 0, 不碰 git/reindex
    args = SimpleNamespace(prev="a", new="b", flag="0", foreground=True)
    assert reindex.cmd_post_checkout(args) == 0


def test_post_checkout_skips_same_head():
    args = SimpleNamespace(prev="abc", new="abc", flag="1", foreground=True)
    assert reindex.cmd_post_checkout(args) == 0


def test_direct_windows_post_commit_fails_closed_before_queue_dispatch(monkeypatch, capsys):
    calls = iter([
        (0, r"D:\WorkSpace\platform"),
        (0, "docs/README.md"),
        (0, _TARGET_COMMIT),
    ])
    monkeypatch.setattr(reindex_commands, "_git_out", lambda *_args: next(calls))
    monkeypatch.setattr(
        reindex_commands,
        "require_local_hook_queue_route",
        lambda _cfg: (_ for _ in ()).throw(LocalHookRelayRequired("use `git hook run post-commit`")),
    )
    monkeypatch.setattr(reindex_commands.C, "config", lambda: {})
    monkeypatch.setattr(
        reindex_commands,
        "_dispatch_reindex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("跨运行时门禁失败后不得打开或写入队列")
        ),
    )

    rc = reindex_commands.cmd_post_commit(SimpleNamespace(foreground=False))

    assert rc == 0
    assert "git hook run post-commit" in capsys.readouterr().err


def test_post_merge_owner_guard_runs_before_dispatch(monkeypatch):
    calls = iter([(0, r"D:\repo"), (0, "orig"), (0, "docs/a.md")])
    monkeypatch.setattr(reindex_commands, "_git_out", lambda *_args: next(calls))
    monkeypatch.setattr(
        reindex_commands,
        "_local_hook_queue_route_ready",
        lambda banner: banner != "post-merge",
    )
    monkeypatch.setattr(
        reindex_commands,
        "_dispatch_reindex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    assert reindex_commands.cmd_post_merge(SimpleNamespace(foreground=False)) == 0


def test_merge_commit_paths_match_post_merge_first_parent_scope(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8",
        )
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "tests@example.invalid")
    git("config", "user.name", "Tests")
    (repo / ".no-hooks").mkdir()
    git("config", "core.hooksPath", ".no-hooks")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs" / "old.md").write_text("indexed", encoding="utf-8")
    git("add", "base.txt", "docs/old.md")
    git("commit", "-m", "base")
    git("checkout", "-b", "feature")
    (repo / "feature.py").write_text("feature = True", encoding="utf-8")
    git("mv", "docs/old.md", "new.bin")
    git("add", "feature.py", "new.bin")
    git("commit", "-m", "feature")
    git("checkout", "main")
    (repo / "main.md").write_text("main", encoding="utf-8")
    git("add", "main.md")
    git("commit", "-m", "main")
    git("merge", "--no-ff", "feature", "-m", "merge")

    rc, planned = commit_changed_paths(repo, "HEAD", git_out=reindex_commands._git_out)
    merge_rc, post_merge = changed_paths_between(
        repo, "ORIG_HEAD", "HEAD", git_out=reindex_commands._git_out,
    )

    assert (rc, merge_rc) == (0, 0)
    assert planned == post_merge
    assert set(planned) == {"docs/old.md", "feature.py", "new.bin"}


def test_fast_forward_integration_paths_cover_all_pulled_commits(tmp_path):
    repo = tmp_path / "ff-repo"
    repo.mkdir()

    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True,
            text=True, encoding="utf-8",
        )
        return result.stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "tests@example.invalid")
    git("config", "user.name", "Tests")
    (repo / ".no-hooks").mkdir()
    git("config", "core.hooksPath", ".no-hooks")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    git("add", "base.txt")
    git("commit", "-m", "base")
    git("checkout", "-b", "feature")
    (repo / "earlier.py").write_text("indexed = True", encoding="utf-8")
    git("add", "earlier.py")
    git("commit", "-m", "indexed")
    (repo / "last.txt").write_text("not indexed", encoding="utf-8")
    git("add", "last.txt")
    git("commit", "-m", "last")
    git("checkout", "main")
    git("merge", "--ff-only", "feature")
    head = git("rev-parse", "HEAD")

    result = integration_changed_paths(repo, head, git_out=reindex_commands._git_out)

    assert result == (0, ["earlier.py", "last.txt"])


def test_post_checkout_owner_guard_runs_before_dispatch(monkeypatch):
    monkeypatch.setattr(
        reindex_commands,
        "_git_out",
        lambda *_args: (0, r"D:\repo") if _args[0] is None else (0, "src/a.py"),
    )
    monkeypatch.setattr(
        reindex_commands,
        "_local_hook_queue_route_ready",
        lambda banner: banner != "post-checkout",
    )
    monkeypatch.setattr(
        reindex_commands,
        "_dispatch_reindex",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    args = SimpleNamespace(prev="a" * 40, new="b" * 40, flag="1", foreground=False)
    assert reindex_commands.cmd_post_checkout(args) == 0


def test_parser_registers_new_hooks():
    from codev_platform.cli import build_parser
    p = build_parser()
    # 三个 hook 子命令都能解析(不抛 SystemExit)
    for argv in (["post-commit"], ["post-merge", "0"], ["post-checkout", "aaa", "bbb", "1"]):
        ns = p.parse_args(argv)
        assert callable(ns.func)


def test_install_hooks_prefers_repo_local_codev_platform(tmp_path):
    repo = tmp_path / "repo"
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True)

    rc = cmd_install_hooks(SimpleNamespace(repo=str(repo)))

    assert rc == 0
    for name, command in (
        ("post-commit", "post-commit"),
        ("post-merge", 'post-merge "$@"'),
        ("post-checkout", 'post-checkout "$@"'),
    ):
        content = (hooks / name).read_text(encoding="utf-8")
        assert '"$repo_root/.venv/Scripts/python.exe"' in content
        assert '"$repo_root/.venv/bin/python"' in content
        assert 'git -C "$repo_root" rev-parse --git-common-dir' in content
        assert 'common_root=$(dirname "$common_dir")' in content
        assert '"$common_root/.venv/Scripts/python.exe"' in content
        assert '"$common_root/.venv/bin/python"' in content
        assert f'exec "$CODEV_PYTHON" -m codev_platform.cli {command}' in content
        assert f'exec codev-platform {command}' in content
        assert content.index('"$repo_root/.venv/Scripts/python.exe"') < content.index(
            'git -C "$repo_root" rev-parse --git-common-dir'
        ) < content.index('"$common_root/.venv/Scripts/python.exe"')


def test_install_hooks_routes_windows_git_bash_to_wsl_without_local_fallback(tmp_path):
    """Windows hook 必须只投递 WSL 正式队列，WSL 失败时不得写回本地旧库。"""
    repo = tmp_path / "repo"
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)

    assert cmd_install_hooks(SimpleNamespace(repo=str(repo))) == 0

    content = (hooks_dir / "post-commit").read_text(encoding="utf-8")
    relay_condition = 'if [ -z "${WSL_DISTRO_NAME:-}" ] && command -v wsl.exe >/dev/null 2>&1; then'
    assert relay_condition in content
    assert 'MSYS_NO_PATHCONV=1 wsl.exe' in content
    assert 'cygpath -am "$repo_root"' in content
    assert 'wslpath -u "$repo_root"' not in content
    assert '/home/helloworld/work/codev-platform/.venv/bin/python' in content
    assert (
        'CODEV_WSL_DATA_DIR="${CODEV_WSL_DATA_DIR:-'
        '/home/helloworld/work/codev-platform/data}"'
    ) in content
    assert 'env PLATFORM_DATA_DIR="$CODEV_WSL_DATA_DIR" "$CODEV_WSL_PYTHON"' in content
    assert content.index(relay_condition) < content.index(
        'CODEV_PYTHON=""'
    )
    wsl_branch = content.split(relay_condition, 1)[1]
    wsl_branch = wsl_branch.split("\nfi\n", 1)[0]
    assert "exit 0" in wsl_branch
    assert "CODEV_PYTHON" not in wsl_branch


def test_post_commit_only_pushes_dev_to_verified_local_wsl_remote(tmp_path):
    """Windows 提交必须先进入本机 WSL Git；远端或分支不符时不得错误入队。"""
    repo = tmp_path / "repo"
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)

    assert cmd_install_hooks(SimpleNamespace(repo=str(repo))) == 0

    post_commit = (hooks_dir / "post-commit").read_text(encoding="utf-8")
    post_merge = (hooks_dir / "post-merge").read_text(encoding="utf-8")
    assert 'CODEV_WSL_REMOTE="${CODEV_WSL_REMOTE:-}"' in post_commit
    assert 'branch."$current_branch".remote' in post_commit
    assert 'CODEV_WSL_BRANCH="${CODEV_WSL_BRANCH:-dev}"' in post_commit
    assert 'current_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD' in post_commit
    assert 'wsl_url=$(git -C "$repo_root" remote get-url "$CODEV_WSL_REMOTE"' in post_commit
    assert 'git -C "$repo_root" push "$CODEV_WSL_REMOTE"' in post_commit
    assert '"HEAD:refs/heads/$CODEV_WSL_BRANCH"' in post_commit
    assert "WSL Git 推送失败，已跳过入队" in post_commit
    assert post_commit.index('git -C "$repo_root" push "$CODEV_WSL_REMOTE"') < post_commit.index(
        "MSYS_NO_PATHCONV=1 wsl.exe"
    )
    assert 'git -C "$repo_root" push "$CODEV_WSL_REMOTE"' not in post_merge


def test_pre_push_audit_prefers_wsl_data_owner_runtime():
    """Windows Git 的图谱门禁必须在 WSL 数据 owner 内执行，避免跨 UNC 读取 SQLite。"""
    script = (
        Path(__file__).resolve().parents[1] / "tools" / "dev" / "pre-push-audit.ps1"
    ).read_text(encoding="ascii")

    assert 'CODEV_WSL_DISTRO' in script
    assert 'CODEV_WSL_USER' in script
    assert '/home/helloworld/work/codev-platform/.venv/bin/python' in script
    assert '/home/helloworld/work/codev-platform/data' in script
    assert 'env "PLATFORM_DATA_DIR=$wslDataDir"' in script
    assert 'graph audit --all' in script
    assert script.index("$wslReady") < script.index("$py = Join-Path")


def test_wsl_relay_is_not_reentered_inside_native_wsl(tmp_path):
    """服务仓 git pull 触发钩子时不得再套一层 wsl.exe。"""
    repo = tmp_path / "repo"
    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)

    assert cmd_install_hooks(SimpleNamespace(repo=str(repo))) == 0

    content = (hooks_dir / "post-merge").read_text(encoding="utf-8")
    assert 'if [ -z "${WSL_DISTRO_NAME:-}" ] && command -v wsl.exe' in content


def test_安装器链接工作树使用版本控制返回的钩子路径(tmp_path, monkeypatch):
    repo = tmp_path / "linked"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /shared/worktrees/linked\n", encoding="utf-8")
    shared_hooks = tmp_path / "main" / ".git" / "hooks"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _git_path(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=str(shared_hooks) + "\n")

    monkeypatch.setattr(hooks, "subprocess", SimpleNamespace(run=_git_path), raising=False)

    rc = cmd_install_hooks(SimpleNamespace(repo=str(repo)))

    assert rc == 0
    assert calls[0][0] == ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"]
    assert (shared_hooks / "post-commit").is_file()
    assert not (repo / ".git" / "hooks").exists()


def test_安装器链接工作树相对钩子路径按仓根解析(tmp_path, monkeypatch):
    repo = tmp_path / "linked"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /shared/worktrees/linked\n", encoding="utf-8")
    relative_hooks = "../main/.git/hooks"
    shared_hooks = (repo / relative_hooks).resolve()

    def _git_path(_argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=relative_hooks + "\n")

    monkeypatch.setattr(hooks.subprocess, "run", _git_path)

    rc = cmd_install_hooks(SimpleNamespace(repo=str(repo)))

    assert rc == 0
    assert shared_hooks.is_absolute()
    assert (shared_hooks / "post-commit").is_file()
    assert not (repo / ".git" / "hooks").exists()


def test_安装器链接工作树路径解析失败安全退出(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "linked"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /shared/worktrees/linked\n", encoding="utf-8")

    def _git_path(_argv, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(hooks, "subprocess", SimpleNamespace(run=_git_path), raising=False)

    rc = cmd_install_hooks(SimpleNamespace(repo=str(repo)))

    assert rc == 1
    assert "FAIL: 无法解析链接工作树的钩子目录" in capsys.readouterr().err
    assert not (repo / ".git" / "hooks").exists()


def test_安装器无法创建钩子目录输出中文错误(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)

    class _无法创建的目录:
        def mkdir(self, **_kwargs):
            raise OSError("denied")

    monkeypatch.setattr(hooks, "_resolve_hooks_dir", lambda *_args: _无法创建的目录())

    rc = cmd_install_hooks(SimpleNamespace(repo=str(repo)))

    assert rc == 1
    assert "FAIL: 无法创建钩子目录" in capsys.readouterr().err


def test_dispatch_enqueues_local_hook_meta(tmp_path, monkeypatch):
    enq: list[tuple[str, str, object]] = []
    resolved: list[object] = []
    queue_options: list[dict[str, object]] = []

    class _Q:
        def enqueue(self, pid, kind, meta=None):
            enq.append((pid, kind, meta))

    def _resolve(repo):
        resolved.append(repo)
        return _TARGET_COMMIT

    def _open_queue(**kwargs):
        queue_options.append(kwargs)
        return _Q()

    monkeypatch.setattr("codev_platform.reindex.target_commit.resolve_repo_head", _resolve)
    monkeypatch.setattr("codev_platform.reindex.open_default_queue", _open_queue)
    monkeypatch.setattr(reindex.C, "project_id_of", lambda repo: "demo-proj")
    monkeypatch.setattr(reindex.C, "config", lambda: {})
    monkeypatch.setattr(reindex.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(
        "codev_platform.ops.reindex.dispatch.impacted_project_ids_for_repo",
        lambda repo, **kw: ["demo-proj"],
    )

    rc = reindex._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="trigger commit: deadbeef",
        banner="post-commit",
    )

    assert rc == 0
    assert [kind for _pid, kind, _meta in enq] == ["codegraph", "ingest", "code_vec"]
    assert all(meta.source == "local_hook" for _pid, _kind, meta in enq)
    assert all(meta.pull_policy == "never" for _pid, _kind, meta in enq)
    assert all(meta.target_commit == _TARGET_COMMIT for _pid, _kind, meta in enq)
    assert resolved == [tmp_path]
    assert queue_options == [{"fail_soft": False}]


def test_dispatch_目标提交解析失败不阻断_hook(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reindex.C, "project_id_of", lambda repo: "demo-proj")
    monkeypatch.setattr(reindex.C, "config", lambda: {})
    monkeypatch.setattr(reindex.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(
        "codev_platform.ops.reindex.dispatch.impacted_project_ids_for_repo",
        lambda repo, **kw: ["demo-proj"],
    )

    def _raise(_repo):
        raise RuntimeError("git 不可用")

    monkeypatch.setattr("codev_platform.reindex.target_commit.resolve_repo_head", _raise)

    rc = reindex._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="trigger commit: deadbeef",
        banner="post-commit",
    )

    assert rc == 0
    assert "reindex 入队失败（已忽略）" in capsys.readouterr().err


def test_dispatch_目标提交失败只记录安全阶段信息(tmp_path, monkeypatch, capsys):
    secret = r"\\wsl.localhost\Ubuntu\home\private\queue"
    monkeypatch.setattr(reindex.C, "project_id_of", lambda repo: "demo-proj")
    monkeypatch.setattr(reindex.C, "config", lambda: {})
    monkeypatch.setattr(reindex.C, "meta_health", lambda pid: {})
    monkeypatch.setattr(
        "codev_platform.ops.reindex.dispatch.impacted_project_ids_for_repo",
        lambda repo, **kw: ["demo-proj"],
    )

    def _raise(_repo):
        raise OSError(5, f"access denied: {secret}")

    monkeypatch.setattr("codev_platform.reindex.target_commit.resolve_repo_head", _raise)

    assert reindex._dispatch_reindex(
        tmp_path,
        ["apps/web/src/Foo.java"],
        foreground=False,
        trigger_line="trigger commit: deadbeef",
        banner="post-commit",
    ) == 0

    output = capsys.readouterr().err
    log = (tmp_path / "tools" / "chroma" / "reindex.log").read_text(encoding="utf-8")
    assert "stage=resolve-target" in output
    assert "stage=resolve-target" in log
    assert "OSError" in output
    assert "errno=5" in output
    assert secret not in output
    assert secret not in log
