"""reindex CodeGraph stage 与多仓协调租约的接线回归。"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from codev_platform.codegraph.operation_lease import (
    CodegraphOperationLeaseBusyError,
    CodegraphOperationLeaseError,
)


@contextmanager
def _协调租约(state: dict[str, object]):
    state["held"] = True
    try:
        yield
    finally:
        state["held"] = False


@contextmanager
def _进入即失败(error: Exception):
    if error:
        raise error
    yield


@contextmanager
def _退出即失败(error: Exception):
    yield
    raise error


def _注入空协调租约(monkeypatch, commands) -> None:
    def leases(_repositories, *, timeout_sec: float):
        assert timeout_sec == 15.0
        return nullcontext()

    monkeypatch.setattr(commands, "codegraph_reindex_leases", leases, raising=False)


def _配置多仓(monkeypatch, main: Path, extra: Path) -> None:
    from codev_platform.core.repos import RepoSpec

    specs = [
        RepoSpec(root=main, tag="", is_main=True, source_project_id="demo"),
        RepoSpec(root=extra, tag="extra", is_main=False, source_project_id="extra"),
    ]
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs",
        lambda _project_id, **_kwargs: specs,
    )
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda *_args, **_kwargs: {"action": "ok"},
    )


def test_多仓只申请一次协调上下文并在全部sync期间持有(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    main = tmp_path / "main"
    extra = tmp_path / "extra"
    main.mkdir()
    extra.mkdir()
    main = main.resolve()
    extra = extra.resolve()
    _配置多仓(monkeypatch, main, extra)
    state: dict[str, object] = {"held": False, "calls": []}

    def leases(repositories, *, timeout_sec: float):
        state["calls"].append((tuple(repositories), timeout_sec))
        return _协调租约(state)

    def run(*_args, **kwargs):
        assert state["held"] is True, "sync 必须位于同一个多仓协调上下文内"
        return SimpleNamespace(returncode=0, cwd=kwargs["cwd"])

    monkeypatch.setattr(commands, "codegraph_reindex_leases", leases, raising=False)
    monkeypatch.setattr(commands.C, "run", run)

    locked, rc = commands._run_codegraph_stage(main, "demo")

    assert (locked, rc) == (False, 0)
    assert state["calls"] == [((main, extra), 15.0)]
    assert state["held"] is False
    assert capsys.readouterr().out.count("proof: codegraph ok") == 1


def test_协调超时返回可重试码且不输出成功证明(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.codegraph.operation_lease import CodegraphOperationLeaseBusyError
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()

    def busy_leases(_repositories, *, timeout_sec: float):
        assert timeout_sec == 15.0
        return _进入即失败(CodegraphOperationLeaseBusyError("不得泄露底层锁细节"))

    monkeypatch.setattr(commands, "codegraph_reindex_leases", busy_leases, raising=False)
    monkeypatch.setattr(
        commands.C,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("协调超时时不得执行 codegraph sync")
        ),
    )

    locked, rc = commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert (locked, rc) == (True, 2)
    assert "proof: codegraph ok" not in output.out
    assert "不得泄露底层锁细节" not in output.out + output.err


def test_真实sync错误保持非零失败码且不输出成功证明(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    state: dict[str, object] = {"held": False, "calls": []}

    def leases(repositories, *, timeout_sec: float):
        state["calls"].append((tuple(repositories), timeout_sec))
        return _协调租约(state)

    monkeypatch.setattr(commands, "codegraph_reindex_leases", leases, raising=False)
    monkeypatch.setattr(
        commands.C,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    locked, rc = commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert (locked, rc) == (False, 1)
    assert state["calls"] == [((repo.resolve(),), 15.0)]
    assert "proof: codegraph ok" not in output.out
    assert "codegraph sync exit=1" in output.err


def test_codegraph_cli缺失返回真实失败且不输出成功证明(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    state: dict[str, object] = {"held": False, "calls": []}

    def leases(repositories, *, timeout_sec: float):
        state["calls"].append((tuple(repositories), timeout_sec))
        return _协调租约(state)

    def missing(command, *, cwd: str):
        assert command == ["codegraph", "sync"]
        assert cwd == str(repo.resolve())
        raise FileNotFoundError("敏感可执行文件路径")

    monkeypatch.setattr(commands, "codegraph_reindex_leases", leases, raising=False)
    monkeypatch.setattr(commands.C, "run", missing)

    locked, rc = commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert (locked, rc) == (False, 1)
    assert "codegraph CLI 不可用" in output.err
    assert "敏感可执行文件路径" not in output.out + output.err
    assert "proof: codegraph ok" not in output.out


def test_协调租约不可用时失败关闭且不泄露底层错误(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.codegraph.operation_lease import CodegraphOperationLeaseError
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()

    def broken_leases(_repositories, *, timeout_sec: float):
        assert timeout_sec == 15.0
        return _进入即失败(CodegraphOperationLeaseError("敏感底层路径"))

    monkeypatch.setattr(commands, "codegraph_reindex_leases", broken_leases, raising=False)
    monkeypatch.setattr(
        commands.C,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("协调租约不可用时不得执行 codegraph sync")
        ),
    )

    locked, rc = commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert (locked, rc) == (False, 1)
    assert "敏感底层路径" not in output.err
    assert "proof: codegraph ok" not in output.out


@pytest.mark.parametrize(
    "error_type",
    [CodegraphOperationLeaseBusyError, CodegraphOperationLeaseError],
)
def test_协调正文租约异常不得误判为协调获取失败(
    error_type,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    _注入空协调租约(monkeypatch, commands)

    def body_error(*_args, **_kwargs):
        raise error_type("正文租约异常")

    monkeypatch.setattr(commands.C, "run", body_error)

    with pytest.raises(error_type, match="正文租约异常"):
        commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert "重建协调超时" not in output.out
    assert "重建协调租约不可用" not in output.err
    assert "proof: codegraph ok" not in output.out


@pytest.mark.parametrize(
    "error_type",
    [CodegraphOperationLeaseBusyError, CodegraphOperationLeaseError],
)
def test_协调退出租约异常不得误判为协调获取失败(
    error_type,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()

    def leases(_repositories, *, timeout_sec: float):
        assert timeout_sec == 15.0
        return _退出即失败(error_type("退出租约异常"))

    monkeypatch.setattr(commands, "codegraph_reindex_leases", leases, raising=False)
    monkeypatch.setattr(
        commands.C,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    with pytest.raises(error_type, match="退出租约异常"):
        commands._run_codegraph_stage(repo.resolve(), None)

    output = capsys.readouterr()
    assert "重建协调超时" not in output.out
    assert "重建协调租约不可用" not in output.err
    assert "proof: codegraph ok" not in output.out


def test_配置文件缺失不得伪报codegraph_cli不可用(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(
        "codev_platform.core.config.load_config",
        lambda: (_ for _ in ()).throw(FileNotFoundError("配置文件缺失")),
    )

    with pytest.raises(FileNotFoundError, match="配置文件缺失"):
        commands._run_codegraph_stage(repo.resolve(), "demo")

    output = capsys.readouterr()
    assert "codegraph CLI 不可用" not in output.err


def test_仓发现文件缺失不得伪报codegraph_cli不可用(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("仓发现文件缺失")),
    )

    with pytest.raises(FileNotFoundError, match="仓发现文件缺失"):
        commands._run_codegraph_stage(repo.resolve(), "demo")

    output = capsys.readouterr()
    assert "codegraph CLI 不可用" not in output.err


def test_ensure_link文件缺失不得伪报codegraph_cli不可用(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from codev_platform.core.repos import RepoSpec
    from codev_platform.ops.reindex import commands

    repo = tmp_path / "repo"
    repo.mkdir()
    resolved = repo.resolve()
    monkeypatch.setattr("codev_platform.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "codev_platform.core.repos.project_repo_specs",
        lambda *_args, **_kwargs: [
            RepoSpec(root=resolved, tag="", is_main=True, source_project_id="demo")
        ],
    )
    monkeypatch.setattr(
        "codev_platform.ops.codegraph.ensure_codegraph_linked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("链接源文件缺失")),
    )
    _注入空协调租约(monkeypatch, commands)
    monkeypatch.setattr(
        commands.C,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ensure link 失败后不得执行 codegraph sync")
        ),
    )

    with pytest.raises(FileNotFoundError, match="链接源文件缺失"):
        commands._run_codegraph_stage(resolved, "demo")

    output = capsys.readouterr()
    assert "codegraph CLI 不可用" not in output.err
