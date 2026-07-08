"""Regression: reindex runner must bound its subprocess with a timeout.

deep-audit-2026-06-03 P2#4: the worker consumes the spool queue serially, so a
single hung reindex subprocess (external CLI / model load / SQLite lock / network
/ git stall) blocks every other project's reindex forever. The runner now wraps
subprocess.run with a config-driven timeout and returns rc=124 on expiry, which
the worker treats as a real failure (rc != 0 and != 2) -> discard, so a
perpetually-hanging job cannot head-of-line block the queue.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from codev_platform.reindex import runners


def _patch_venv(monkeypatch) -> None:
    monkeypatch.setattr(runners, "_venv_python", lambda cfg: "python")


def test_runner_passes_config_timeout_to_subprocess(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {"reindex": {"runner_timeout_sec": 42}})

    assert rc == 0
    assert captured["timeout"] == 42.0


def test_runner_returns_124_on_timeout(monkeypatch, tmp_path):
    def fake_run(cmd, timeout=None, **kwargs):
        kwargs["stdout"].write("loading model\nstill running\n")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {})

    assert rc == runners._TIMEOUT_RC == 124
    assert "timeout" in r.last_note
    assert "still running" in r.last_note


def test_default_timeout_used_when_unset(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("codegraph", "--codegraph").run("demo", Path("."), {})

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


def test_non_positive_timeout_disables_bound(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": 0}}
    )

    assert captured["timeout"] is None


def test_invalid_timeout_falls_back_to_default(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(cmd, timeout=None, **kwargs):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": "not-a-number"}}
    )

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


def test_runner_records_failure_output_tail(monkeypatch, capsys, tmp_path):
    def fake_run(cmd, timeout=None, **kwargs):
        kwargs["stdout"].write("ok line\nFAIL: chroma reindex exit=1\n")

        class _R:
            returncode = 1

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

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

    def fake_run(cmd, timeout=None, **kwargs):
        kwargs["stdout"].write("first failure\n")

        class _R:
            returncode = returncodes.pop(0)

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    assert r.run("demo", Path("."), {}) == 1
    assert r.last_note
    assert r.run("demo", Path("."), {}) == 0
    assert r.last_note == ""


def test_runner_keeps_success_output_in_runner_log(monkeypatch, tmp_path):
    def fake_run(cmd, timeout=None, **kwargs):
        kwargs["stdout"].write("WARN: non-fatal lane skipped\n")

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setenv("PLATFORM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("ingest", "--ingest")
    assert r.run("demo", Path("."), {}) == 0

    assert r.last_note == ""
    assert "WARN: non-fatal lane skipped" in (
        tmp_path / "logs" / "reindex-runner" / "demo__ingest.log"
    ).read_text(encoding="utf-8")
