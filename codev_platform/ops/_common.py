"""Shared helpers for codev_platform.ops -- cross-platform, config-driven.

Replaces the Windows-only .ps1 plumbing: path resolution comes from
~/.codev-platform/config.json (via core.config) or git/relative resolution;
subprocess launching tolerates Windows script launchers (.cmd/.bat/.ps1) and runs
real executables directly everywhere. No hardcoded machine paths.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from codev_platform.core.config import load_config, get as _cfg_get


# ----------------------------------------------------------------------
# print helpers
# ----------------------------------------------------------------------
def out(msg: str = "") -> None:
    print(msg, flush=True)


def err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def config() -> dict[str, Any]:
    return load_config()


def cfg_get(dotted_key: str, default: Any = None, cfg: dict[str, Any] | None = None) -> Any:
    """Read a dotted key from config.json, e.g. cfg_get('runtime.chroma_venv')."""
    return _cfg_get(cfg if cfg is not None else load_config(), dotted_key, default)


# ----------------------------------------------------------------------
# repo / package roots
# ----------------------------------------------------------------------
def codev_root() -> Path:
    """The codev-platform repo root (this package's parent)."""
    return Path(__file__).resolve().parents[2]


def resolve_repo(repo: str | os.PathLike | None = None) -> Path:
    """Resolve the target repo root.

    Explicit ``repo`` wins; otherwise ``git rev-parse --show-toplevel`` from cwd
    (cross-platform). Raises RuntimeError if neither yields a repo.
    """
    if repo:
        return Path(repo).expanduser().resolve()
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            # 显式 utf-8 解码：防仓位于中文路径时 GBK 解码崩(2026-06-02 GBK 扫尾)
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip()).resolve()
    except FileNotFoundError:
        pass
    raise RuntimeError("not a git repo (and no --repo given); run inside the target repo")


def project_id_of(repo: Path) -> str | None:
    """Read project_id from <repo>/.claude/project.json."""
    pj = repo / ".claude" / "project.json"
    if pj.is_file():
        try:
            return json.loads(pj.read_text(encoding="utf-8")).get("project_id")
        except Exception:
            return None
    return None


def meta_health(project_id: str | None) -> dict[str, Any]:
    """Load platform_meta/projects/<id>/meta.json 'health' section ({} if absent)."""
    if not project_id:
        return {}
    meta = codev_root() / "platform_meta" / "projects" / project_id / "meta.json"
    if meta.is_file():
        try:
            return json.loads(meta.read_text(encoding="utf-8")).get("health") or {}
        except Exception:
            return {}
    return {}


# ----------------------------------------------------------------------
# reindex-scope pattern matching (single source of truth: meta.health.*)
# Generic project-name-free defaults; each project EXTENDS via meta.
# ----------------------------------------------------------------------
DEFAULT_DOC_PATTERNS = [
    r"^docs/.*\.md$", r"^\.claude/(rules|skills)/.*\.md$",
    r"^apps/[^/]+/\.claude/rules/.*\.md$", r"^tools/.*\.md$",
    r".*CLAUDE\.md$", r".*AGENTS\.md$", r"^README\.md$",
]
DEFAULT_CODEGRAPH_PATTERNS = [r"^apps/[^/]+/src/.*\.(java|ts|tsx)$"]


def reindex_patterns(health: dict[str, Any]) -> dict[str, list[str]]:
    """Per-scope regex patterns = generic defaults + project's meta.health.reindex_*."""
    def ext(key: str, defaults: list[str]) -> list[str]:
        extra = health.get(key) or []
        return list(defaults) + list(extra)
    return {
        "doc": ext("reindex_doc_patterns", DEFAULT_DOC_PATTERNS),
        "codegraph": ext("reindex_codegraph_patterns", DEFAULT_CODEGRAPH_PATTERNS),
    }


def matches_any(path: str, patterns: list[str]) -> bool:
    import re
    p = path.replace("\\", "/")
    return any(re.search(pat, p) for pat in patterns)


# ----------------------------------------------------------------------
# python interpreters (from config; never hardcoded)
# ----------------------------------------------------------------------
def _venv_python(venv_dir: str | Path) -> Path:
    venv = Path(venv_dir)
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def chroma_python() -> Path | None:
    """Python in the chroma venv (config runtime.chroma_venv). None if unset/missing."""
    v = cfg_get("runtime.chroma_venv")
    if not v:
        return None
    py = _venv_python(v)
    return py if py.exists() else py  # return path even if missing; caller Tests existence


# ----------------------------------------------------------------------
# cross-platform subprocess (absorbs the old _winexec)
# ----------------------------------------------------------------------
def resolve_argv(cmd: list[str]) -> list[str]:
    """argv that CreateProcess/exec can launch on this OS.

    Windows: route .cmd/.bat through ``cmd /c`` and .ps1 through ``powershell -File``
    (Python 3.12+ won't exec script launchers from a list argv). Elsewhere / real
    executables: direct.
    """
    if not cmd:
        return cmd
    exe = shutil.which(cmd[0]) or cmd[0]
    rest = list(cmd[1:])
    if os.name == "nt":
        low = exe.lower()
        if low.endswith((".cmd", ".bat")):
            return [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe, *rest]
        if low.endswith(".ps1"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", exe, *rest]
    return [exe, *rest]


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run tolerant of .cmd/.bat/.ps1 launchers (mvn, pnpm) on Windows."""
    return subprocess.run(resolve_argv(cmd), **kwargs)
