"""executor 到真实 runner 子进程的冻结仓向量传播测试。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.core import config, repos
from codev_platform.core.runtime_interpreter import proven_interpreter_path
from codev_platform.core.runtime_models import RuntimeIdentity
from codev_platform.reindex import runner_logs, runners


def _main_spec(root: Path) -> repos.RepoSpec:
    return repos.RepoSpec(root=root, tag="", is_main=True, source_project_id="demo")


def _write_chroma_proof(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("proof: chroma ok\n", encoding="utf-8")


def _bind_attempt_context(cfg: dict, attempt_id: str = "attempt-1") -> None:
    identity = RuntimeIdentity(
        mode="installed",
        runtime_revision="a" * 64,
        release_id=None,
        wheel_sha256=None,
        base_id=None,
        base_requirements_sha256=None,
        interpreter_realpath=str(Path(sys.executable).resolve()),
        environment_prefix=str(Path(sys.prefix).resolve()),
        source_root=None,
    )
    runners.bind_attempt_context(
        cfg,
        attempt_id,
        runtime_revision=identity.runtime_revision,
        interpreter=proven_interpreter_path(identity),
    )


def test_process_cfg_override_freezes_repo_resolution(tmp_path: Path) -> None:
    frozen = tmp_path / "frozen"
    live = tmp_path / "live"
    frozen.mkdir()
    live.mkdir()
    cfg = {"projects": {"demo": {"repo_path": str(live)}}}

    repos.install_runtime_repo_override(cfg, "demo", [_main_spec(frozen)])
    actual = repos.project_repo_specs("demo", cfg=cfg)

    assert [item.root for item in actual] == [frozen.resolve()]


def test_repo_override_environment_contains_no_config_secret(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    secret = "postgresql://user:raw-secret@db/platform"
    cfg: dict = {"memory": {"pg_dsn": secret}, "tokens": {"admin": "raw-secret"}}
    repos.install_runtime_repo_override(cfg, "demo", [_main_spec(root)])

    environment = repos.runtime_repo_override_environment(cfg, "demo")
    digest_environment = config.reindex_config_snapshot_environment(cfg)
    encoded = environment[repos.REINDEX_REPO_OVERRIDE_ENV]

    assert "raw-secret" not in encoded
    assert "pg_dsn" not in encoded
    assert set(json.loads(encoded)) == {
        "schema_version",
        "project_id",
        "config_sha256",
        "repos",
    }
    assert secret not in digest_environment[config.REINDEX_CONFIG_DIGEST_ENV]
    assert len(digest_environment[config.REINDEX_CONFIG_DIGEST_ENV]) == 64


def test_private_attempt_state_does_not_change_public_config_digest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cfg: dict = {"projects": {"demo": {"repo_path": str(root)}}}
    before = config.config_snapshot_digest(cfg)

    repos.install_runtime_repo_override(cfg, "demo", [_main_spec(root)])
    _bind_attempt_context(cfg)

    assert config.config_snapshot_digest(cfg) == before


def test_real_runner_uses_proven_interpreter_and_propagates_frozen_repos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    frozen = tmp_path / "frozen"
    live = tmp_path / "live"
    frozen.mkdir()
    live.mkdir()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"projects": {"demo": {"repo_path": str(live)}}}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, *, timeout, log_path, env=None, **_kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        _write_chroma_proof(log_path)
        return 0

    monkeypatch.setenv("CODEV_PLATFORM_CONFIG", str(config_path))
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    cfg = config.load_config()
    repos.install_runtime_repo_override(cfg, "demo", [_main_spec(frozen)])
    _bind_attempt_context(cfg)
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    monkeypatch.setattr(
        runners,
        "_platform_runtime_python",
        lambda: pytest.fail("隔离 runner 不得选择平台解释器"),
    )

    runner = runners.CliReindexRunner("chroma", "--chroma")
    assert runner.run("demo", frozen, cfg) == 0

    command = captured["cmd"]
    child_env = captured["env"]
    assert isinstance(command, list)
    assert Path(command[0]).resolve() == Path(sys.executable).resolve()
    assert "--proven-runtime" in command
    assert isinstance(child_env, dict)
    assert repos.REINDEX_REPO_OVERRIDE_ENV in child_env
    assert config.REINDEX_CONFIG_DIGEST_ENV in child_env

    script = (
        "import json; "
        "from codev_platform.core.config import load_config; "
        "from codev_platform.core.repos import project_repo_specs; "
        "print(json.dumps([str(x.root) for x in "
        "project_repo_specs('demo', cfg=load_config())]))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=10,
    )

    assert json.loads(completed.stdout) == [str(frozen.resolve())]

    config_path.write_text(
        json.dumps({
            "projects": {"demo": {"repo_path": str(live)}},
            "reindex": {"worker_auto_start": False},
        }),
        encoding="utf-8",
    )
    changed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    assert changed.returncode != 0
    assert "RuntimeError: reindex" in changed.stderr
    assert "raw-secret" not in changed.stderr


def test_invalid_child_override_fails_closed_without_live_config_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    live = tmp_path / "live"
    live.mkdir()
    cfg = {"projects": {"demo": {"repo_path": str(live)}}}
    monkeypatch.setenv(repos.REINDEX_REPO_OVERRIDE_ENV, '{"schema_version":1,"project_id":"other"}')

    with pytest.raises(ValueError, match="override|覆盖"):
        repos.project_repo_specs("demo", cfg=cfg)


def test_invalid_config_snapshot_digest_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(config.REINDEX_CONFIG_DIGEST_ENV, "not-a-sha256")

    with pytest.raises(RuntimeError, match="摘要无效"):
        config.load_config()


def _child_override_payload(root: Path, digest: object = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "project_id": "demo",
        "config_sha256": digest,
        "repos": [{
            "root": str(root.resolve()),
            "tag": "",
            "is_main": True,
            "source_project_id": "demo",
        }],
    }


@pytest.mark.parametrize(
    ("environment_digest", "payload_digest", "remove_payload_digest"),
    [
        (None, None, False),
        (None, "a" * 64, False),
        ("a" * 64, None, False),
        ("a" * 64, True, False),
        ("not-a-sha256", "not-a-sha256", False),
        ("A" * 64, "A" * 64, False),
        ("a" * 64, "a" * 64, True),
    ],
)
def test_child_override_rejects_missing_null_bool_or_invalid_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    environment_digest: str | None,
    payload_digest: object,
    remove_payload_digest: bool,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    payload = _child_override_payload(root, payload_digest)
    if remove_payload_digest:
        payload.pop("config_sha256")
    monkeypatch.setenv(repos.REINDEX_REPO_OVERRIDE_ENV, json.dumps(payload))
    if environment_digest is None:
        monkeypatch.delenv(config.REINDEX_CONFIG_DIGEST_ENV, raising=False)
    else:
        monkeypatch.setenv(config.REINDEX_CONFIG_DIGEST_ENV, environment_digest)

    with pytest.raises(ValueError, match="摘要|字段|快照|绑定"):
        repos.project_repo_specs("demo", cfg={})


def test_child_override_rejects_valid_digest_that_does_not_match_cfg(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cfg = {"projects": {"demo": {"repo_path": str(root)}}}
    foreign_digest = "a" * 64
    assert config.config_snapshot_digest(cfg) != foreign_digest
    monkeypatch.setenv(
        repos.REINDEX_REPO_OVERRIDE_ENV,
        json.dumps(_child_override_payload(root, foreign_digest)),
    )
    monkeypatch.setenv(config.REINDEX_CONFIG_DIGEST_ENV, foreign_digest)

    with pytest.raises(ValueError, match="摘要"):
        repos.project_repo_specs("demo", cfg=cfg)


def test_runner_does_not_mutate_parent_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    cfg: dict = {}
    repos.install_runtime_repo_override(cfg, "demo", [_main_spec(root)])
    _bind_attempt_context(cfg)

    def fake_run(_cmd, *, log_path, **_kwargs):
        _write_chroma_proof(log_path)
        return 0

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv(repos.REINDEX_REPO_OVERRIDE_ENV, raising=False)
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    assert runners.CliReindexRunner("chroma", "--chroma").run("demo", root, cfg) == 0
    assert repos.REINDEX_REPO_OVERRIDE_ENV not in os.environ
