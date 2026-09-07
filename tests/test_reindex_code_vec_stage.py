"""reindex 的 code_vec stage、runner 与锁忙处置回归。"""

from __future__ import annotations

from pathlib import Path

from tests.reindex_ingest_stage_support import (
    R,
    _args,
    _repo_fixture,
    _report,
    _stub_stages,
    _固定_dispatch目标提交,
    _预置普通ingest测试的Linux门禁,
)

_共享夹具 = (_repo_fixture, _固定_dispatch目标提交, _预置普通ingest测试的Linux门禁)


def test_default_runs_code_vec_stage(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    called = {}
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda project_id, **kwargs: (
            called.update(
                project_id=project_id,
                incremental=kwargs.get("incremental"),
            )
            or 3
        ),
    )

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 0
    assert called["project_id"] == "demo-proj"
    assert called["incremental"] is True


def test_code_vec_force_is_full(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda _project_id, **kwargs: seen.update(incremental=kwargs.get("incremental")) or 0,
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), force=True))

    assert rc == 0
    assert seen["incremental"] is False


def test_code_vec_success_emits_proof_marker(_repo, monkeypatch, capsys):
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.build_code_vector_index",
        lambda _project_id, **_kwargs: 0,
    )

    rc = R.cmd_reindex(_args(repo=str(_repo), code_vec=True))

    assert rc == 0
    assert "proof: code_vec ok" in capsys.readouterr().out


def test_code_vec_failure_isolated(_repo, monkeypatch, capsys):
    _stub_stages(monkeypatch)
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )

    def boom(_project_id, **_kwargs):
        raise RuntimeError("embed daemon down")

    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", boom)

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 0
    assert "proof: code_vec ok" not in capsys.readouterr().out


def test_code_vec_runner_registered():
    from codev_platform.reindex.runners import kinds

    assert "code_vec" in kinds()


def test_code_vec_runner_command_only_uses_code_vec_flag(monkeypatch):
    import codev_platform.reindex.runners as runners

    captured = {}
    monkeypatch.setattr(runners, "_platform_runtime_python", lambda: "py")
    monkeypatch.setattr(
        runners.runner_logs,
        "run_logged_process",
        lambda command, **kwargs: (
            captured.update(command=command),
            Path(kwargs["log_path"]).parent.mkdir(parents=True, exist_ok=True),
            Path(kwargs["log_path"]).write_text("proof: code_vec ok\n", encoding="utf-8"),
            0,
        )[-1],
    )

    runners.get_runner("code_vec").run("demo-proj", Path("/repo"), {})

    assert "--code-vec" in captured["command"]
    assert "--codegraph" not in captured["command"]


def test_codegraph_rc2立即结束且不执行后续阶段(_repo, monkeypatch):
    from codev_platform.ops.reindex import commands

    calls: list[str] = []

    def codegraph(*_args):
        calls.append("codegraph")
        return True, 2

    def later(*_args, **_kwargs):
        raise AssertionError("CodeGraph 可重试后不得执行后续索引阶段")

    monkeypatch.setattr(commands, "_run_codegraph_stage", codegraph)
    monkeypatch.setattr(commands, "_run_chroma_stage", later)
    monkeypatch.setattr(commands, "_run_ingest_stage", later)
    monkeypatch.setattr(commands, "_run_code_vector_stage", later)

    rc = commands.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 2
    assert calls == ["codegraph"]


def test_code_vec_lock_busy_returns_rc2(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )
    from codev_platform.recall.code_vector_store import CodeVecLockBusy

    def busy(_project_id, **_kwargs):
        raise CodeVecLockBusy("another build running")

    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", busy)

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 2


def test_code_vec_generic_failure_still_rc0(_repo, monkeypatch):
    _stub_stages(monkeypatch)
    monkeypatch.setattr(
        "codev_platform.graph.ingest.ingest_project",
        lambda _repo, _project_id, **_kwargs: _report(),
    )

    def boom(_project_id, **_kwargs):
        raise RuntimeError("embed daemon down")

    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", boom)

    rc = R.cmd_reindex(_args(repo=str(_repo)))

    assert rc == 0


def test_code_vec_generic_failure_proven_runtime_returns_rc1(monkeypatch, capsys):
    """隔离 worker 必须终态失败，禁止静默完成或无限重试阻塞队列。"""
    from codev_platform.ops.reindex import commands

    def boom(_project_id, **_kwargs):
        raise RuntimeError("完整性探针失败")

    monkeypatch.setattr("codev_platform.recall.code_vector_store.build_code_vector_index", boom)

    rc = commands._build_code_vector_stage(
        _args(force=False, proven_runtime=True),
        "demo-proj",
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL: code vector failed" in captured.err
    assert "proof: code_vec ok" not in captured.out
