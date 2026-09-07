"""隔离 reindex 的解释器、运行版本与项目身份绑定回归。"""
from __future__ import annotations

import os
import sys
import argparse
import json
import subprocess
from pathlib import Path

import pytest

from codev_platform.core.runtime_interpreter import (
    current_proven_interpreter_path,
    proven_interpreter_path,
)
from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.core import config, repos
from codev_platform.ops import reindex as reindex_ops
from codev_platform.reindex import runner_logs, runners


def _identity(prefix: Path, interpreter_realpath: Path) -> RuntimeIdentity:
    return RuntimeIdentity(
        mode="installed",
        runtime_revision="a" * 64,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath=str(interpreter_realpath),
        environment_prefix=str(prefix),
        source_root=None,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv 最后一跳符号链接语义")
def test_proven_interpreter_keeps_venv_link_and_ignores_current_retarget(tmp_path: Path) -> None:
    system_python = tmp_path / "system" / "python3"
    system_python.parent.mkdir()
    system_python.write_text("", encoding="utf-8")
    old_prefix = tmp_path / "releases" / "old" / "venv"
    new_prefix = tmp_path / "releases" / "new" / "venv"
    for prefix in (old_prefix, new_prefix):
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "python").symlink_to(system_python)

    current = tmp_path / "current"
    current.symlink_to(old_prefix.parent, target_is_directory=True)
    lexical_executable = current / "venv" / "bin" / "python"
    identity = _identity(old_prefix.resolve(), system_python.resolve())

    current.unlink()
    current.symlink_to(new_prefix.parent, target_is_directory=True)

    selected = proven_interpreter_path(
        identity,
        executable=lexical_executable,
        process_prefix=current / "venv",
    )

    assert selected == old_prefix / "bin" / "python"
    assert selected.is_symlink()
    assert selected.resolve() == system_python.resolve()


def test_proven_interpreter_rejects_identity_target_mismatch(tmp_path: Path) -> None:
    prefix = tmp_path / "venv"
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    executable = prefix / relative
    executable.parent.mkdir(parents=True)
    executable.write_text("", encoding="utf-8")
    identity = _identity(prefix.resolve(), tmp_path / "other-python")

    with pytest.raises(ValueError, match="解释器"):
        proven_interpreter_path(
            identity,
            executable=executable,
            process_prefix=prefix,
        )


def test_current_process_proven_interpreter_preserves_environment_prefix() -> None:
    identity = _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve())

    selected = proven_interpreter_path(identity)

    assert selected.resolve() == Path(sys.executable).resolve()
    assert selected.is_relative_to(Path(sys.prefix).resolve())


def test_current_proven_interpreter复用当前运行身份(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve())
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: identity,
    )

    assert current_proven_interpreter_path() == proven_interpreter_path(identity)


def test_isolated_runner_propagates_bound_runtime_project_and_interpreter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cfg: dict = {}
    repos.install_runtime_repo_override(
        cfg,
        "demo",
        [repos.RepoSpec(root=root, tag="", is_main=True, source_project_id="demo")],
    )
    runtime_revision = "a" * 64
    proven_python = proven_interpreter_path(
        _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve()),
    )
    runners.bind_attempt_context(
        cfg,
        "attempt-1",
        runtime_revision=runtime_revision,
        interpreter=proven_python,
        target_commit="b" * 40,
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, *, log_path, env=None, **_kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("proof: chroma ok\n", encoding="utf-8")
        return 0

    monkeypatch.setenv("PLATFORM_PROJECT_ID", "other")
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    rc = runners.CliReindexRunner("chroma", "--chroma").run("demo", root, cfg)

    assert rc == 0
    command = captured["cmd"]
    environment = captured["env"]
    assert isinstance(command, list)
    assert command[0] == str(proven_python)
    assert command[command.index("--expected-project-id") + 1] == "demo"
    assert command[command.index("--expected-runtime-revision") + 1] == runtime_revision
    assert isinstance(environment, dict)
    assert environment["PLATFORM_PROJECT_ID"] == "demo"
    assert environment["CODEV_REINDEX_EXPECTED_RUNTIME_REVISION"] == runtime_revision
    assert environment["CODEV_REINDEX_TARGET_COMMIT"] == "b" * 40


def test_isolated_runner_without_bound_runtime_context_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cfg: dict = {}
    repos.install_runtime_repo_override(
        cfg,
        "demo",
        [repos.RepoSpec(root=root, tag="", is_main=True, source_project_id="demo")],
    )
    monkeypatch.setattr(
        runner_logs,
        "run_logged_process",
        lambda *_args, **_kwargs: pytest.fail("缺少证明上下文时不得启动子进程"),
    )

    runner = runners.CliReindexRunner("chroma", "--chroma")

    assert runner.run("demo", root, cfg) == 1
    assert "运行" in runner.last_note or "上下文" in runner.last_note


def _reindex_args(repo: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "repo": str(repo),
        "chroma": True,
        "codegraph": False,
        "ingest": False,
        "code_vec": False,
        "force": False,
        "proven_runtime": True,
        "expected_project_id": "demo",
        "expected_runtime_revision": "a" * 64,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _declared_repo(root: Path, project_id: object) -> Path:
    config_dir = root / ".codex"
    config_dir.mkdir(parents=True)
    (config_dir / "project.json").write_text(
        json.dumps({"project_id": project_id}),
        encoding="utf-8",
    )
    return root


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("declared_project", [None, "other", "Demo", 123])
def test_proven_cli_rejects_cross_project_before_any_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    declared_project: object,
) -> None:
    repo = _declared_repo(tmp_path, declared_project)
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo")
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve()),
    )
    monkeypatch.setattr(
        reindex_ops.C,
        "run",
        lambda *_args, **_kwargs: pytest.fail("项目身份不一致时不得执行索引 stage"),
    )

    assert reindex_ops.cmd_reindex(_reindex_args(repo)) == 1


def test_proven_cli_rejects_runtime_revision_drift_before_any_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _declared_repo(tmp_path, "demo")
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo")
    monkeypatch.setenv("CODEV_REINDEX_EXPECTED_RUNTIME_REVISION", "b" * 64)
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve()),
    )
    monkeypatch.setattr(
        reindex_ops.C,
        "run",
        lambda *_args, **_kwargs: pytest.fail("运行版本不一致时不得执行索引 stage"),
    )

    args = _reindex_args(repo, expected_runtime_revision="b" * 64)

    assert reindex_ops.cmd_reindex(args) == 1


def test_proven_cli_rejects_missing_frozen_input_context_before_any_stage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = _declared_repo(tmp_path, "demo")
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo")
    monkeypatch.setenv("CODEV_REINDEX_EXPECTED_RUNTIME_REVISION", "a" * 64)
    monkeypatch.delenv("CODEV_REINDEX_PROVEN_INPUTS", raising=False)
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve()),
    )
    monkeypatch.setattr(
        reindex_ops.C,
        "run",
        lambda *_args, **_kwargs: pytest.fail("缺少冻结输入时不得执行索引 stage"),
    )

    assert reindex_ops.cmd_reindex(_reindex_args(repo)) == 1


def test_proven_cli_rejects_nested_repo_that_is_not_frozen_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "测试")
    _git(root, "config", "user.email", "test@example.invalid")
    _declared_repo(root, "demo")
    nested = root / "codev_platform"
    nested.mkdir()
    (nested / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _declared_repo(nested, "demo")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "测试主仓")
    machine_config = tmp_path / "config.json"
    machine_config.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(root)}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(machine_config))
    monkeypatch.setenv("PLATFORM_PROJECT_ID", "demo")
    monkeypatch.setenv("CODEV_REINDEX_PROVEN_INPUTS", "1")
    monkeypatch.setenv("CODEV_REINDEX_EXPECTED_RUNTIME_REVISION", "a" * 64)
    cfg = config.load_config()
    repos.install_runtime_repo_override(
        cfg,
        "demo",
        [repos.RepoSpec(root=root.resolve(), tag="", is_main=True, source_project_id="demo")],
    )
    for key, value in config.reindex_config_snapshot_environment(cfg).items():
        monkeypatch.setenv(key, value)
    for key, value in repos.runtime_repo_override_environment(cfg, "demo").items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        "codev_platform.core.runtime_identity.runtime_identity",
        lambda: _identity(Path(sys.prefix).resolve(), Path(sys.executable).resolve()),
    )
    monkeypatch.setattr(
        reindex_ops.C,
        "run",
        lambda *_args, **_kwargs: pytest.fail("错误 --repo 不得执行任何索引 stage"),
    )

    assert reindex_ops.cmd_reindex(_reindex_args(nested)) == 1
