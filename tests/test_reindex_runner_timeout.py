"""Regression: reindex runner must bound its subprocess with a timeout.

deep-audit-2026-06-03 P2#4: the worker consumes the spool queue serially, so a
single hung reindex subprocess (external CLI / model load / SQLite lock / network
/ git stall) blocks every other project's reindex forever. The runner now wraps
its delegated CLI process with a config-driven timeout and returns rc=124 on
expiry, which the worker treats as a real failure (rc != 0 and != 2) -> discard,
so a perpetually-hanging job cannot head-of-line block the queue.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from codev_platform.reindex import runner_logs, runners


def _patch_venv(monkeypatch) -> None:
    monkeypatch.setattr(runners, "_platform_runtime_python", lambda: "python")


def _write_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(text, encoding="utf-8")


def test_runner_passes_config_timeout_to_subprocess(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {"reindex": {"runner_timeout_sec": 42}})

    assert rc == 0
    assert captured["timeout"] == 42.0


def test_legacy_runner使用平台解释器而非chroma配置(
    monkeypatch,
    tmp_path,
):
    captured: dict = {}

    def fake_run(cmd, *, log_path, **_kwargs):
        captured["cmd"] = cmd
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    monkeypatch.setattr(runners, "_platform_runtime_python", lambda: "platform-python")
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))

    assert runners.CliReindexRunner("chroma", "--chroma").run("demo", Path("."), {}) == 0
    assert captured["cmd"][:3] == ["platform-python", "-I", "-m"]
    assert "--proven-runtime" not in captured["cmd"]


def test_isolated_attempt_uses_unique_hashed_log_path(monkeypatch, tmp_path):
    captured: dict = {}
    cfg: dict = {}

    def fake_run(_cmd, *, log_path, **_kwargs):
        captured["log_path"] = log_path
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    interpreter = Path(sys.executable).parent.resolve() / Path(sys.executable).name
    runners.bind_attempt_context(
        cfg,
        "attempt-secret-name",
        runtime_revision="a" * 64,
        interpreter=interpreter,
    )

    runner = runners.CliReindexRunner("chroma", "--chroma")
    assert runner.run("demo", Path("."), cfg) == 0

    name = captured["log_path"].name
    assert name.startswith("demo__chroma__")
    assert "attempt-secret-name" not in name


def test_runner_returns_124_on_timeout(monkeypatch, tmp_path):
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "loading model\nstill running\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {})

    assert rc == runners._TIMEOUT_RC == 124
    assert "timeout" in r.last_note
    assert "still running" in r.last_note


def test_隔离codegraph_runner保留rc2且不执行成功证明改判(monkeypatch, tmp_path):
    from codev_platform.core import repos

    repo = tmp_path / "repo"
    repo.mkdir()
    cfg: dict = {}
    repos.install_runtime_repo_override(
        cfg,
        "demo",
        [repos.RepoSpec(root=repo, tag="", is_main=True, source_project_id="demo")],
    )
    interpreter = Path(sys.executable).parent.resolve() / Path(sys.executable).name
    runners.bind_attempt_context(
        cfg,
        "attempt-rc2",
        runtime_revision="a" * 64,
        interpreter=interpreter,
        target_commit="b" * 40,
    )
    captured: dict[str, object] = {}

    def fake_run(cmd, *, log_path=None, **_kwargs):
        captured["cmd"] = cmd
        _write_log(log_path, "WARN: CodeGraph 重建协调超时；本 job 将重试\n")
        return 2

    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    runner = runners.CliReindexRunner("codegraph", "--codegraph")

    assert runner.run("demo", repo, cfg) == 2
    assert "--proven-runtime" in captured["cmd"]
    assert "codegraph rc=2" in runner.last_note
    assert "proof marker missing" not in runner.last_note


def test_code_vec_timeout_with_checkpoint_is_released_for_resume(monkeypatch, tmp_path):
    """已落 checkpoint 的 code_vec 超时可重试，不能像无进度卡死任务一样丢弃。"""
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "code vector checkpointed\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    progress = iter([{}, {"fingerprint-1": 64}])
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: next(progress),
    )

    runner = runners.CodeVecReindexRunner()

    assert runner.run("demo", Path("."), {}) == 2
    assert "checkpoint" in runner.last_note


def test_code_vec_nonzero_with_checkpoint_progress_is_released_for_resume(monkeypatch, tmp_path):
    """瞬态 compaction 等非零退出只在严格 checkpoint 确实推进时续跑。"""
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, "FAIL: Error in compaction: Error purging logs\n")
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    progress = iter([{"fingerprint-1": 64}, {"fingerprint-1": 128}])
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: next(progress),
    )

    runner = runners.CodeVecReindexRunner()

    assert runner.run("demo", Path("."), {}) == 2
    assert "checkpoint" in runner.last_note


def test_code_vec_nonzero_without_checkpoint_progress_is_terminal(monkeypatch, tmp_path):
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, "FAIL: deterministic failure\n")
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: {"fingerprint-1": 64},
    )

    assert runners.CodeVecReindexRunner().run("demo", Path("."), {}) == 1


def test_code_vec_revoked_current_manifest_is_released_once_for_side_build(
    monkeypatch, tmp_path,
):
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, "FAIL: code_vec 完整性失败\n")
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: {},
    )
    manifests = iter([True, False, False, False])
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_current_manifest_exists",
        lambda _project_id: next(manifests),
    )

    runner = runners.CodeVecReindexRunner()
    assert runner.run("demo", Path("."), {}) == 2
    assert "side-build" in runner.last_note
    assert runner.run("demo", Path("."), {}) == 1


def test_code_vec_checkpoint_rollback_is_terminal_to_prevent_retry_oscillation(
    monkeypatch, tmp_path,
):
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, "FAIL: checkpoint collection IDs mismatch\n")
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    snapshots = iter([{"fingerprint-1": 64}, {"fingerprint-1": 32}])
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: next(snapshots),
    )

    runner = runners.CodeVecReindexRunner()
    assert runner.run("demo", Path("."), {}) == 1


def test_code_vec_timeout_without_new_checkpoint_is_not_retried(monkeypatch, tmp_path):
    """已有旧 checkpoint 但本次零推进时必须终止，不能无限重复同一批。"""
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "same checkpoint\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: {"fingerprint-1": 64},
    )

    assert runners.CodeVecReindexRunner().run("demo", Path("."), {}) == 124


def test_code_vec_timeout_changed_build_id_without_higher_fingerprint_progress_is_terminal(
    monkeypatch, tmp_path,
):
    """side-build 换目录但同指纹高水位不增长时，仍必须终止而非无限续跑。"""
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "rebuilt same checkpoint\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)
    snapshots = iter([
        {"strict-fingerprint": 64},
        {"strict-fingerprint": 64},
    ])
    monkeypatch.setattr(
        "codev_platform.recall.code_vector_store.code_vec_checkpoint_progress",
        lambda _project_id: next(snapshots),
    )

    assert runners.CodeVecReindexRunner().run("demo", Path("."), {}) == 124


def test_code_vec_cpu_uses_extended_bounded_runner_timeout(monkeypatch, tmp_path):
    """CPU 向量全量构建允许跨过通用 30 分钟门槛，但仍受专用上限保护。"""
    captured: dict = {}

    def fake_run(_cmd, *, timeout=None, log_path=None, **_kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "code vector ok: 1 节点 (re)embedded\nproof: code_vec ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATFORM_EMBED_DEVICE", "cpu")
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    assert runners.CodeVecReindexRunner().run("demo", Path("."), {}) == 0
    assert captured["timeout"] == 10800.0


def test_chroma_cpu_uses_extended_bounded_runner_timeout(monkeypatch, tmp_path):
    """CPU 文档全量嵌入可跨过通用 30 分钟，但仍受两小时上限保护。"""
    captured: dict = {}

    def fake_run(_cmd, *, timeout=None, log_path=None, **_kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PLATFORM_EMBED_DEVICE", "cpu")
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    assert runners.ChromaReindexRunner().run("demo", Path("."), {}) == 0
    assert captured["timeout"] == 7200.0


def test_cpu_timeout_is_selected_from_config_without_environment(monkeypatch):
    """非 systemd 调用也必须尊重配置中的 CPU 设备，不能退回通用 30 分钟。"""
    monkeypatch.delenv("PLATFORM_EMBED_DEVICE", raising=False)
    cfg = {"models": {"embed_device": "cpu"}}

    assert runners.ChromaReindexRunner().runner_timeout(cfg) == 7200.0
    assert runners.CodeVecReindexRunner().runner_timeout(cfg) == 10800.0


def test_invalid_cpu_scoped_timeout_falls_back_to_cpu_bound(monkeypatch):
    """专用配置写坏时仍保持 CPU 安全上限，不能悄悄缩回通用 30 分钟。"""
    monkeypatch.delenv("PLATFORM_EMBED_DEVICE", raising=False)
    cfg = {
        "models": {"embed_device": "cpu"},
        "reindex": {"chroma_runner_timeout_sec": "invalid"},
        "recall": {"code_vec": {"runner_timeout_sec": -1}},
    }

    assert runners.ChromaReindexRunner().runner_timeout(cfg) == 7200.0
    assert runners.CodeVecReindexRunner().runner_timeout(cfg) == 10800.0


def test_code_vec_qwen_local_uses_its_actual_configured_device(monkeypatch):
    """qwen-local 的设备真值属于 code_vec 配置，不得被 daemon 的全局环境覆盖。"""
    monkeypatch.setenv("PLATFORM_EMBED_DEVICE", "cpu")
    cfg = {
        "recall": {"code_vec": {"embed_backend": "qwen-local", "embed_device": "cuda"}},
    }

    assert runners.CodeVecReindexRunner().runner_timeout(cfg) == 1800.0


def test_default_timeout_used_when_unset(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "proof: codegraph ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    runners.CliReindexRunner("codegraph", "--codegraph").run("demo", Path("."), {})

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


@pytest.mark.parametrize(
    "configured", [0, -1, float("nan"), float("inf"), 1e308, True],
)
def test_non_positive_timeout_falls_back_to_safe_default(monkeypatch, tmp_path, configured):
    captured: dict = {}

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": configured}}
    )

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


def test_invalid_timeout_falls_back_to_default(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        captured["timeout"] = timeout
        _write_log(log_path, "proof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": "not-a-number"}}
    )

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


def test_runner_records_failure_output_tail(monkeypatch, capsys, tmp_path):
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "ok line\nFAIL: chroma reindex exit=1\n")
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {})

    assert rc == 1
    assert "chroma rc=1" in r.last_note
    assert "FAIL: chroma reindex exit=1" in r.last_note
    assert "FAIL: chroma reindex exit=1" in capsys.readouterr().err
    assert "FAIL: chroma reindex exit=1" in (
        tmp_path / "logs" / "reindex-runner" / "demo__chroma.log"
    ).read_text(encoding="utf-8")


def test_runner_clears_previous_failure_note_on_next_run(monkeypatch, tmp_path):
    returncodes = [1, 0]

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        rc = returncodes.pop(0)
        _write_log(log_path, "first failure\n" if rc else "proof: chroma ok\n")
        return rc

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    assert r.run("demo", Path("."), {}) == 1
    assert r.last_note
    assert r.run("demo", Path("."), {}) == 0
    assert r.last_note == ""


def test_runner_keeps_success_output_in_runner_log(monkeypatch, tmp_path):
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "WARN: non-fatal lane skipped\nproof: chroma ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    assert r.run("demo", Path("."), {}) == 0

    assert r.last_note == ""
    assert "WARN: non-fatal lane skipped" in (
        tmp_path / "logs" / "reindex-runner" / "demo__chroma.log"
    ).read_text(encoding="utf-8")


def test_runner_failure_note_uses_redacted_log_tail(monkeypatch, tmp_path):
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        buf = runner_logs.RunnerLogBuffer()
        buf.feed_text("FAIL: token=raw-secret password=local-pass\n")
        buf.write_to(log_path)
        return 1

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    assert r.run("demo", Path("."), {}) == 1

    assert "raw-secret" not in r.last_note
    assert "local-pass" not in r.last_note
    assert "token=<redacted>" in r.last_note
    assert "password=<redacted>" in r.last_note


def test_runner_accepts_proven_success_markers(monkeypatch, tmp_path):
    outputs = {
        "chroma": "proof: chroma ok\n",
        "codegraph": "codegraph sync [main] /repo\nproof: codegraph ok\n",
        "ingest": "graph ingest ok: 2 plugin(s) -> store [builtin.linker]\nproof: ingest ok\n",
        "code_vec": "code vector ok: 0 节点 (re)embedded\nproof: code_vec ok\n",
    }

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        flag = next((part for part in cmd if part in {"--chroma", "--codegraph", "--ingest", "--code-vec"}), "")
        kind = {"--chroma": "chroma", "--codegraph": "codegraph", "--ingest": "ingest", "--code-vec": "code_vec"}[flag]
        _write_log(log_path, outputs[kind])
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    for kind, flag in {
        "chroma": "--chroma",
        "codegraph": "--codegraph",
        "ingest": "--ingest",
        "code_vec": "--code-vec",
    }.items():
        r = runners.CliReindexRunner(kind, flag)
        assert r.run("demo", Path("."), {}) == 0
        assert r.last_note == ""


def test_chroma_runner_rejects_rc_zero_without_storage_proof(monkeypatch, tmp_path):
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, "完成: 但没有存储证明\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    runner = runners.CliReindexRunner("chroma", "--chroma")

    assert runner.run("demo", Path("."), {}) == 1
    assert "proof marker missing" in runner.last_note


@pytest.mark.parametrize(
    "output",
    [
        "diagnostic: proof: chroma okay was not emitted\n",
        "proof: chroma ok\nproof: chroma ok\n",
    ],
)
def test_chroma_runner_rejects_non_unique_or_embedded_marker(monkeypatch, tmp_path, output):
    def fake_run(_cmd, *, log_path=None, **_kwargs):
        _write_log(log_path, output)
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    assert runners.CliReindexRunner("chroma", "--chroma").run("demo", Path("."), {}) == 1


def test_runner_scans_middle_of_full_stream_not_truncated_log(monkeypatch, tmp_path):
    def fake_run(_cmd, *, log_path=None, output_observer=None, **_kwargs):
        full = (
            b"proof: chroma ok\n"
            + b"x" * 300_000
            + b"\nproof: chroma ok\n"
            + b"y" * 300_000
        )
        for offset in range(0, len(full), 8192):
            output_observer(full[offset:offset + 8192])
        _write_log(log_path, "proof: chroma ok\n...<truncated>...\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    assert runners.CliReindexRunner("chroma", "--chroma").run("demo", Path("."), {}) == 1


def test_runner_converts_failsoft_output_to_failure(monkeypatch, tmp_path):
    outputs = {
        "codegraph": "WARN: codegraph sync skipped (main, MCP holds DB); continuing other repos\n",
        "ingest": "WARN: graph ingest failed (non-fatal, baseline indexes unaffected): boom\n",
        "code_vec": "WARN: code vector failed (non-fatal, baseline indexes unaffected): boom\n",
    }

    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        flag = next((part for part in cmd if part in {"--codegraph", "--ingest", "--code-vec"}), "")
        kind = {"--codegraph": "codegraph", "--ingest": "ingest", "--code-vec": "code_vec"}[flag]
        _write_log(log_path, outputs[kind])
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    for kind, flag in {
        "codegraph": "--codegraph",
        "ingest": "--ingest",
        "code_vec": "--code-vec",
    }.items():
        r = runners.CliReindexRunner(kind, flag)
        assert r.run("demo", Path("."), {}) == 1
        assert "proof failed" in r.last_note


def test_code_vec_proof_ignores_codegraph_markers(monkeypatch, tmp_path):
    def fake_run(cmd, *, timeout=None, log_path=None, **kwargs):
        _write_log(log_path, "code vector ok: 0 节点 (re)embedded\nproof: code_vec ok\n")
        return 0

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(runner_logs, "run_logged_process", fake_run)

    r = runners.CliReindexRunner("code_vec", "--code-vec")
    assert r.run("demo", Path("."), {}) == 0
    assert r.last_note == ""
