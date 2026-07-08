"""F18: reindex --codegraph 跑 sync 前自动幂等 ensure .codegraph junction(免手动 link --all)。

用 monkeypatch 替掉 link_state / link_project / _is_link, 在 tmp 目录验证:
  - 已 linked → 跳过, 不重建;
  - 未 link → 触发 link_project;
  - link 抛异常 → fail-soft 不抛, 返回 action=error;
  - CodegraphReindexRunner 在 sync(super().run) 前调 ensure_codegraph_linked。
不碰真 junction。
"""
from __future__ import annotations

from pathlib import Path

from codev_platform.ops import codegraph as cg
from codev_platform.reindex import runners


def test_ensure_link_skips_when_already_linked(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "link_state", lambda repo_cg, plat: "linked")
    called = {"linked": False}

    def _fail_link(*a, **k):
        called["linked"] = True
        raise AssertionError("不应在 already-linked 时重建")

    monkeypatch.setattr(cg, "link_project", _fail_link)
    r = cg.ensure_codegraph_linked("p1", tmp_path / "repo", {})
    assert r["action"] == "already-linked"
    assert called["linked"] is False


def test_ensure_link_triggers_when_not_linked(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "link_state", lambda repo_cg, plat: "in-repo")
    seen = {}

    def _link(cfg, pid, repo):
        seen["pid"] = pid
        return {"pid": pid, "action": "moved+linked"}

    monkeypatch.setattr(cg, "link_project", _link)
    r = cg.ensure_codegraph_linked("p1", tmp_path / "repo", {})
    assert r["action"] == "moved+linked"
    assert seen["pid"] == "p1"


def test_ensure_link_fail_soft_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "link_state", lambda repo_cg, plat: "in-repo")

    def _boom(*a, **k):
        raise OSError("mklink /J 失败")

    monkeypatch.setattr(cg, "link_project", _boom)
    r = cg.ensure_codegraph_linked("p1", tmp_path / "repo", {})  # 不抛
    assert r["action"] == "error"
    assert "mklink" in r["note"]


def test_codegraph_runner_ensures_link_before_sync(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda pid, repo, cfg: order.append("ensure") or {"action": "already-linked"},
    )
    monkeypatch.setattr(
        runners.CliReindexRunner, "run",
        lambda self, pid, repo, cfg: order.append("sync") or 0,
    )
    runner = runners.CodegraphReindexRunner()
    rc = runner.run("p1", Path(tmp_path), {})
    assert rc == 0
    assert order == ["ensure", "sync"]  # ensure-link 在 sync 之前


def test_codegraph_runner_fails_when_ensure_link_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda pid, repo, cfg: {"action": "error", "note": "junction denied"},
    )
    monkeypatch.setattr(
        runners.CliReindexRunner, "run",
        lambda self, pid, repo, cfg: (_ for _ in ()).throw(AssertionError("sync should not run")),
    )

    runner = runners.CodegraphReindexRunner()
    rc = runner.run("p1", Path(tmp_path), {})

    assert rc == 1
    assert "ensure-link failed" in runner.last_note
    assert "junction denied" in runner.last_note


def test_codegraph_runner_registered():
    r = runners.get_runner("codegraph")
    assert isinstance(r, runners.CodegraphReindexRunner)
