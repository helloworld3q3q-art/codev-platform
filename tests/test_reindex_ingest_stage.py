"""reindex 的统一图谱 ingest 与 codegraph stage 回归。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests.reindex_ingest_stage_support import (
    R,
    _args,
    _failed_report,
    _repo_fixture,
    _report,
    _stub_stages,
    _固定_dispatch目标提交,
    _预置普通ingest测试的Linux门禁,
)

_共享夹具 = (_repo_fixture, _固定_dispatch目标提交, _预置普通ingest测试的Linux门禁)


def test_proven_runtime_chroma_uses_current_interpreter(_repo, monkeypatch):
    commands: list[list[str]] = []
    revision = "a" * 64

    class _CompletedProcess:
        returncode = 0

    monkeypatch.setattr(
        R.C,
        "chroma_python",
        lambda: pytest.fail("隔离 runner 不得读取 legacy chroma_venv"),
    )
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo-proj")
    monkeypatch.setenv("CODEV_REINDEX_EXPECTED_RUNTIME_REVISION", revision)
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: type("Identity", (), {"runtime_revision": revision})(),
    )
    monkeypatch.setattr(
        "codev_platform.core.repo_input_guard.proven_repo_input_guard",
        lambda: type("Guard", (), {"validate_main_root": lambda _self, root: root})(),
    )
    monkeypatch.setattr(
        R.C, "run", lambda command, **_kwargs: commands.append(command) or _CompletedProcess()
    )

    rc = R.cmd_reindex(
        _args(
            repo=str(_repo),
            chroma=True,
            proven_runtime=True,
            expected_project_id="demo-proj",
            expected_runtime_revision=revision,
        )
    )

    assert rc == 0
    assert commands[0][0] == os.path.abspath(sys.executable)
    assert commands[0][1:4] == ["-I", "-m", "codev_platform.chroma.indexer"]


def test_default_runs_ingest_stage(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    called = {}

    def fake_ingest(repo, project_id, **_kwargs):
        called["repo"] = Path(repo)
        called["project_id"] = project_id
        return _report(ingested=["fake.plugin"])

    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project", fake_ingest)

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 0
    assert called["project_id"] == "demo-proj"
    assert called["repo"] == _repo


def test_ingest_only_skips_other_stages(_repo, monkeypatch):
    ran = {"run": False}
    monkeypatch.setattr(
        R.C,
        "run",
        lambda *_args, **_kwargs: (
            ran.__setitem__("run", True) or type("CompletedProcess", (), {"returncode": 0})()
        ),
    )
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True))

    assert rc == 0
    assert ran["run"] is False


def test_ingest_success_emits_proof_marker(_repo, monkeypatch, capsys):
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True))

    assert rc == 0
    assert capsys.readouterr().out.count("proof: ingest ok") == 1


def test_ingest_structured_failure_legacy_has_no_marker(_repo, monkeypatch, capsys):
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _failed_report(),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True))

    captured = capsys.readouterr()
    assert rc == 0
    assert "proof: ingest ok" not in captured.out
    assert "reindex ok" not in captured.out
    assert "legacy 兼容降级" in captured.out
    assert "WARN: graph ingest failed（未完成）" in captured.err


def test_ingest_structured_failure_rejects_plugin_spoofed_marker(_repo, monkeypatch, capsys):
    """插件抢先打印 marker 时，结构化失败告警仍必须让完整流证明失败。"""
    from codev_platform.reindex.runner_proof import RunnerProofScanner

    def spoofed_failure(*_args, **_kwargs):
        print("proof: ingest ok", flush=True)
        return _failed_report()

    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project", spoofed_failure)

    assert R.cmd_reindex(_args(repo=str(_repo), ingest=True)) == 0

    captured = capsys.readouterr()
    scanner = RunnerProofScanner("ingest")
    scanner.feed_bytes((captured.out + captured.err).encode("utf-8"))
    assert "graph ingest warning" in scanner.finish()


def test_ingest_structured_failure_proven_returns_rc1(_repo, monkeypatch, capsys):
    monkeypatch.setattr(
        "codev_platform.ops.reindex.commands._bound_reindex_project",
        lambda _args, _repo: "demo-proj",
    )
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _failed_report(),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True, proven_runtime=True))

    captured = capsys.readouterr()
    assert rc == 1
    assert "proof: ingest ok" not in captured.out
    assert "reindex 失败 (rc=1)" in captured.out
    assert "WARN: graph ingest failed（未完成）" in captured.err


def test_ingest_exception_proven_returns_rc1(_repo, monkeypatch, capsys):
    monkeypatch.setattr(
        "codev_platform.ops.reindex.commands._bound_reindex_project",
        lambda _args, _repo: "demo-proj",
    )
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), ingest=True, proven_runtime=True))

    captured = capsys.readouterr()
    assert rc == 1
    assert "proof: ingest ok" not in captured.out
    assert "WARN: graph ingest failed" in captured.err


def test_codegraph_stage_syncs_all_repo_specs(_repo, tmp_path, monkeypatch):
    from codev_platform.core.repos import RepoSpec

    extra = tmp_path / "extra"
    extra.mkdir()
    specs = [
        RepoSpec(root=_repo.resolve(), tag="", is_main=True, source_project_id="demo-proj"),
        RepoSpec(root=extra.resolve(), tag="extra", is_main=False, source_project_id="extra-proj"),
    ]
    calls: list[Path] = []
    links: list[tuple[str, Path]] = []

    class _CompletedProcess:
        returncode = 0

    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs", lambda _project_id, **_kwargs: specs
    )
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda project_id, repo, _config: (
            links.append((project_id, Path(repo))) or {"action": "ok"}
        ),
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        R.C,
        "run",
        lambda _command, **kwargs: calls.append(Path(kwargs["cwd"])) or _CompletedProcess(),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), codegraph=True))

    assert rc == 0
    assert calls == [_repo.resolve(), extra.resolve()]
    assert links == [("demo-proj", _repo.resolve()), ("extra-proj", extra.resolve())]


def test_codegraph_success_emits_proof_marker(_repo, monkeypatch, capsys):
    class _CompletedProcess:
        returncode = 0

    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda _project_id, _repo, _config: {"action": "ok"},
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(R.C, "run", lambda _command, **_kwargs: _CompletedProcess())

    rc = R.cmd_reindex(_args(repo=str(_repo), codegraph=True))

    assert rc == 0
    assert "proof: codegraph ok" in capsys.readouterr().out


def test_codegraph原生锁忙立即rc2且停止后续仓(_repo, tmp_path, monkeypatch, capsys):
    from contextlib import nullcontext

    from codev_platform.core.repos import RepoSpec
    from codev_platform.ops.reindex import commands

    ok_extra = tmp_path / "ok-extra"
    ok_extra.mkdir()
    specs = [
        RepoSpec(root=_repo.resolve(), tag="", is_main=True, source_project_id="demo-proj"),
        RepoSpec(
            root=ok_extra.resolve(), tag="ok-extra", is_main=False, source_project_id="ok-proj"
        ),
    ]
    calls: list[Path] = []

    class _CompletedProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode

    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs", lambda _project_id, **_kwargs: specs
    )
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda _project_id, _repo, _config: {"action": "ok"},
    )
    monkeypatch.setattr(
        "codev_platform.reindex.git_sync.sync_repo_to_remote",
        lambda _repo: (_ for _ in ()).throw(AssertionError("stage 不应隐式 git sync")),
    )

    def run(_command, **kwargs):
        cwd = Path(kwargs["cwd"])
        calls.append(cwd)
        return _CompletedProcess(2)

    monkeypatch.setattr(
        commands,
        "codegraph_reindex_leases",
        lambda _repositories, *, timeout_sec: nullcontext(),
    )
    monkeypatch.setattr(R.C, "run", run)

    locked, rc = commands._run_codegraph_stage(_repo.resolve(), "demo-proj")

    assert (locked, rc) == (True, 2)
    assert calls == [_repo.resolve()]
    assert "proof: codegraph ok" not in capsys.readouterr().out


def test_ingest_failure_isolated(_repo, monkeypatch):
    _stub_stages(monkeypatch)

    def boom(_repo, _project_id, **_kwargs):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr("codev_platform.graph.ingest.ingest_project", boom)

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 0


def test_ingest_skipped_without_project_id(tmp_path, monkeypatch):
    _stub_stages(monkeypatch)
    seen = {"called": False}
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda *_args, **_kwargs: seen.__setitem__("called", True),
    )

    rc = R.cmd_reindex(_args(repo=str(tmp_path), ingest=True))

    assert rc == 0
    assert seen["called"] is False


def test_ingest_runner_registered():
    from codev_platform.reindex.runners import kinds

    assert "ingest" in kinds()
