"""Regression: cross-link reindex must never scan one repo and write another's DB.

Root cause (pre-fix): the cross_link build scanners live in the BUSINESS repo
(`<repo>/tools/cross_link/`) and derive their scan-root from `__file__`
(schema.REPO_ROOT) while the DB write-path comes from PLATFORM_PROJECT_ID. The
reindex stage pinned neither, so running it for project B could scan repo A's
tree (via the only build_index on PYTHONPATH) yet write B's DB -> cross-tenant
pollution. Also: a repo without scanners (e.g. codev-platform) errored instead
of skipping.

Fix (codev-platform side, tested here):
- skip the stage when `<repo>/tools/cross_link/build_index.py` is absent
- otherwise pin PLATFORM_PROJECT_ID + CROSS_LINK_REPO_ROOT + cwd to the target
  repo so scan-root and write-path stay in lockstep.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import types
from pathlib import Path

import codev_platform.ops.reindex as R
import codev_platform.ops._common as C


def _args(repo: Path) -> argparse.Namespace:
    return argparse.Namespace(
        repo=str(repo), chroma=False, codegraph=False, cross_link=True, force=False
    )


def _mk_repo(tmp_path: Path, pid: str, *, with_builder: bool) -> Path:
    repo = tmp_path / pid
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "project.json").write_text(
        json.dumps({"project_id": pid}), encoding="utf-8"
    )
    if with_builder:
        cl = repo / "tools" / "cross_link"
        cl.mkdir(parents=True)
        (cl / "build_index.py").write_text("# stub\n", encoding="utf-8")
    return repo


def test_skip_when_repo_has_no_scanner(tmp_path, monkeypatch, capsys):
    """codev-platform-like repo (no tools/cross_link/build_index.py) -> skip, no subprocess."""
    repo = _mk_repo(tmp_path, "codev-platform", with_builder=False)

    called = {"n": 0}

    def _boom(*a, **k):  # any subprocess launch is a bug for this repo
        called["n"] += 1
        raise AssertionError("must not spawn cross_link build for a repo without scanners")

    monkeypatch.setattr(C, "run", _boom)
    rc = R.cmd_reindex(_args(repo))
    assert rc == 0
    assert called["n"] == 0
    assert "skipped" in capsys.readouterr().out.lower()


def test_pins_project_and_scan_root_to_target_repo(tmp_path, monkeypatch):
    """Repo WITH scanner -> subprocess env pins pid + scan-root + cwd to that repo."""
    repo = _mk_repo(tmp_path, "openclaw-stock", with_builder=True)
    seen: dict = {}

    def _capture(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        seen["cwd"] = kwargs.get("cwd")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(C, "run", _capture)
    monkeypatch.setattr(C, "cross_link_python", lambda: "python")

    rc = R.cmd_reindex(_args(repo))
    assert rc == 0
    assert seen["cmd"][1:] == ["-m", "cross_link.build_index"]
    # scan-root and write-path both pinned to THIS project's repo (no divergence)
    assert seen["env"]["PLATFORM_PROJECT_ID"] == "openclaw-stock"
    assert Path(seen["env"]["CROSS_LINK_REPO_ROOT"]) == repo
    assert Path(seen["env"]["PYTHONPATH"]) == repo / "tools"
    assert Path(seen["cwd"]) == repo
