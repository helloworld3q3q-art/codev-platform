"""install-hooks -- cross-platform git hook installer (replaces install-git-hooks.ps1).

The old .ps1 stub hardcoded the Windows powershell.exe path, which broke on
Mac/Linux. This installs a portable sh stub that simply execs the
``codev-platform`` console script (on PATH everywhere via pip). pre-push remains
.ps1-based and is only installed when the openclaw-specific
tools/dev/pre-push-audit.ps1 exists.
"""
from __future__ import annotations

import os
from pathlib import Path

from codev_platform.ops._common import out, err, resolve_repo


# Portable hook stubs. Pure LF -- CRLF makes sh.exe choke. Hooks run from the repo
# root, and `codev-platform` (pip console script) is on PATH on all OSes.
# post-commit: 自己提交后更新本地索引。
# post-merge / post-checkout: pull/merge/切分支"获取新代码"后也更新本地索引(双实例 plan P0)。
# "$@" 透传 git 传给 hook 的位置参(post-merge=is-squash; post-checkout=prev new flag)。
POST_COMMIT_STUB = b"#!/bin/sh\nexec codev-platform post-commit\n"
POST_MERGE_STUB = b'#!/bin/sh\nexec codev-platform post-merge "$@"\n'
POST_CHECKOUT_STUB = b'#!/bin/sh\nexec codev-platform post-checkout "$@"\n'

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

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

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
