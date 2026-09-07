from __future__ import annotations

import json
from types import SimpleNamespace

from codev_platform.ops.reindex import commands


def _dirty_repo(monkeypatch, tmp_path) -> None:
    calls = iter([(0, str(tmp_path)), (0, " M docs/changed.md")])
    monkeypatch.setattr(commands, "_git_out", lambda *_args: next(calls))
    monkeypatch.setattr(commands.C, "project_id_of", lambda _repo: "demo")
    monkeypatch.setattr(commands.C, "meta_health", lambda _pid: {})
    monkeypatch.setattr(
        commands.C,
        "reindex_patterns",
        lambda _health: {"doc": [r"\.md$"], "codegraph": []},
    )


def test_dirty_check_json_never_authorizes_manual_reindex(monkeypatch, tmp_path, capsys):
    _dirty_repo(monkeypatch, tmp_path)

    rc = commands.cmd_dirty_check(SimpleNamespace(json=True, quiet=False))

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "commit" in payload["recommendation"]
    assert "run reindex" not in payload["recommendation"].lower()


def test_dirty_check_human_guidance_denies_rebuild_from_dirty_alone(
    monkeypatch,
    tmp_path,
    capsys,
):
    _dirty_repo(monkeypatch, tmp_path)

    rc = commands.cmd_dirty_check(SimpleNamespace(json=False, quiet=False))

    output = capsys.readouterr().out
    assert rc == 1
    assert "post-commit hook" in output
    assert "never authorizes a manual rebuild" in output
    assert "codev-platform reindex` manually" not in output
