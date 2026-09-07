"""跨平台 Git hook 安装器。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from codev_platform.ops._common import out, err, resolve_repo


# 可移植钩子桩必须保持 LF；显式解析仓根，避免嵌套工作目录误用环境入口。
# post-commit: 自己提交后更新本地索引。
# post-merge / post-checkout: pull/merge/切分支"获取新代码"后也更新本地索引(双实例 plan P0)。
# "$@" 透传 git 传给 hook 的位置参(post-merge=is-squash; post-checkout=prev new flag)。
_REPO_ROOT = "repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)\n"

_CLI_RESOLVER = """CODEV_PYTHON=""
for candidate in "$repo_root/.venv/Scripts/python.exe" "$repo_root/.venv/bin/python"; do
  if [ -x "$candidate" ]; then
    CODEV_PYTHON="$candidate"
    break
  fi
done
if [ -z "$CODEV_PYTHON" ]; then
  common_dir=$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null) || common_dir=""
  if [ -n "$common_dir" ]; then
    case "$common_dir" in
      /*|[A-Za-z]:/*) ;;
      *) common_dir="$repo_root/$common_dir" ;;
    esac
    common_root=$(dirname "$common_dir")
    for candidate in "$common_root/.venv/Scripts/python.exe" "$common_root/.venv/bin/python"; do
      if [ -x "$candidate" ]; then
        CODEV_PYTHON="$candidate"
        break
      fi
    done
  fi
fi
"""


def _windows_wsl_push_guard() -> str:
    """只允许 dev 提交推送到已验证的本机 WSL Git 远端。"""
    return (
        '  CODEV_WSL_BRANCH="${CODEV_WSL_BRANCH:-dev}"\n'
        '  current_branch=$(git -C "$repo_root" symbolic-ref --quiet --short HEAD 2>/dev/null) '
        '|| current_branch=""\n'
        '  if [ "$current_branch" != "$CODEV_WSL_BRANCH" ]; then\n'
        '    echo "[codev-hook] 当前分支不是 $CODEV_WSL_BRANCH，跳过 WSL 推送与入队" >&2\n'
        '    exit 0\n'
        '  fi\n'
        '  CODEV_WSL_REMOTE="${CODEV_WSL_REMOTE:-}"\n'
        '  if [ -z "$CODEV_WSL_REMOTE" ]; then\n'
        '    CODEV_WSL_REMOTE=$(git -C "$repo_root" config --get '
        '"branch.\"$current_branch\".remote" 2>/dev/null) || CODEV_WSL_REMOTE=""\n'
        '  fi\n'
        '  wsl_url=$(git -C "$repo_root" remote get-url "$CODEV_WSL_REMOTE" 2>/dev/null) '
        '|| wsl_url=""\n'
        '  case "$wsl_url" in\n'
        '    http://localhost:*|https://localhost:*|http://127.0.0.1:*|https://127.0.0.1:*|'
        'http://*@localhost:*|https://*@localhost:*|http://*@127.0.0.1:*|'
        'https://*@127.0.0.1:*) ;;\n'
        '    *) echo "[codev-hook] WSL 远端不是本机地址，已拒绝推送与入队" >&2; exit 0 ;;\n'
        '  esac\n'
        '  if ! git -C "$repo_root" push "$CODEV_WSL_REMOTE" '
        '"HEAD:refs/heads/$CODEV_WSL_BRANCH"; then\n'
        '    echo "[codev-hook] WSL Git 推送失败，已跳过入队" >&2\n'
        '    exit 0\n'
        '  fi\n'
    )


def _wsl_relay(command: str, *, preflight: str = "") -> str:
    """生成 Windows Git Bash 到 WSL 正式运行时的失败关闭路由。"""
    return (
        'if [ -z "${WSL_DISTRO_NAME:-}" ] && command -v wsl.exe >/dev/null 2>&1; then\n'
        '  CODEV_WSL_DISTRO="${CODEV_WSL_DISTRO:-Ubuntu}"\n'
        '  CODEV_WSL_USER="${CODEV_WSL_USER:-helloworld}"\n'
        '  CODEV_WSL_PYTHON="${CODEV_WSL_PYTHON:-/home/helloworld/work/codev-platform/.venv/bin/python}"\n'
        '  CODEV_WSL_DATA_DIR="${CODEV_WSL_DATA_DIR:-/home/helloworld/work/codev-platform/data}"\n'
        '  windows_repo=$(cygpath -am "$repo_root" 2>/dev/null)\n'
        '  drive=$(printf "%.1s" "$windows_repo" | tr "A-Z" "a-z")\n'
        '  suffix=${windows_repo#?:}\n'
        '  wsl_repo="/mnt/$drive$suffix"\n'
        '  [ -n "$wsl_repo" ] || { echo "[codev-hook] WSL repo path resolution failed" >&2; exit 0; }\n'
        f'{preflight}'
        '  MSYS_NO_PATHCONV=1 wsl.exe -d "$CODEV_WSL_DISTRO" -u "$CODEV_WSL_USER" '
        '--cd "$wsl_repo" env PLATFORM_DATA_DIR="$CODEV_WSL_DATA_DIR" "$CODEV_WSL_PYTHON" '
        '-I -m codev_platform.cli '
        f'{command} || echo "[codev-hook] WSL enqueue failed; local fallback disabled" >&2\n'
        '  exit 0\n'
        'fi\n'
    )


def _codev_platform_stub(command: str, *, wsl_preflight: str = "") -> bytes:
    return (
        f'#!/bin/sh\n{_REPO_ROOT}{_wsl_relay(command, preflight=wsl_preflight)}{_CLI_RESOLVER}'
        'if [ -n "$CODEV_PYTHON" ]; then\n'
        f'  (exec "$CODEV_PYTHON" -m codev_platform.cli {command})\n'
        '  exit 0\n'
        'fi\n'
        f'(exec codev-platform {command})\n'
        'exit 0\n'
    ).encode()


POST_COMMIT_STUB = _codev_platform_stub(
    "post-commit",
    wsl_preflight=_windows_wsl_push_guard(),
)
POST_MERGE_STUB = _codev_platform_stub('post-merge "$@"')
POST_CHECKOUT_STUB = _codev_platform_stub('post-checkout "$@"')

# pre-push stub still routes to the repo's own .ps1 audit (openclaw-specific gates).
PRE_PUSH_STUB = (
    b"#!/bin/sh\n"
    b'exec powershell -NoProfile -ExecutionPolicy Bypass -File "tools/dev/pre-push-audit.ps1"\n'
)


def _write_hook(hooks_dir: Path, name: str, content: bytes) -> Path:
    """Write a hook stub with pure-LF bytes; overwrite if present; make executable."""
    dst = hooks_dir / name
    dst.write_bytes(content)
    try:
        os.chmod(dst, 0o755)
    except OSError:
        pass  # chmod is a no-op / may fail on Windows; sh.exe ignores the bit anyway
    return dst


def _resolve_hooks_dir(repo: Path, git_dir: Path) -> Path | None:
    """返回普通仓或 linked worktree 的 hooks 目录。"""
    if git_dir.is_dir():
        return git_dir / "hooks"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    raw_path = result.stdout.strip()
    if not raw_path:
        return None
    hooks_dir = Path(raw_path)
    return (hooks_dir if hooks_dir.is_absolute() else repo / hooks_dir).resolve()


def cmd_install_hooks(args) -> int:
    try:
        repo = resolve_repo(getattr(args, "repo", None))
    except RuntimeError as e:
        err(f"FAIL: {e}")
        return 1

    git_dir = repo / ".git"
    if not git_dir.exists():
        err(f"FAIL: not a git repo (no .git): {repo}")
        return 1

    hooks_dir = _resolve_hooks_dir(repo, git_dir)
    if hooks_dir is None:
        err("FAIL: 无法解析链接工作树的钩子目录")
        return 1
    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        err("FAIL: 无法创建钩子目录")
        return 1

    out(f"Installing git hooks into: {repo}")

    installed = 0
    for name, stub in (
        ("post-commit", POST_COMMIT_STUB),
        ("post-merge", POST_MERGE_STUB),
        ("post-checkout", POST_CHECKOUT_STUB),
    ):
        dst = _write_hook(hooks_dir, name, stub)
        out(f"  installed: {name} -> {dst}")
        installed += 1

    if (repo / "tools" / "dev" / "pre-push-audit.ps1").is_file():
        dst = _write_hook(hooks_dir, "pre-push", PRE_PUSH_STUB)
        out(f"  installed: pre-push -> {dst}")
        installed += 1
    else:
        out("  skip: pre-push (no tools/dev/pre-push-audit.ps1 in this repo)")

    out("")
    out(f"SUMMARY: {installed} hook(s) installed into {repo}")
    return 0


def register(subparsers) -> None:
    sp = subparsers.add_parser("install-hooks", help="装跨平台 git hooks")
    sp.add_argument("--repo", default=None)
    sp.set_defaults(func=cmd_install_hooks)
