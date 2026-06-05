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


def test_runner_passes_config_timeout_to_subprocess(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, timeout=None):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {"reindex": {"runner_timeout_sec": 42}})

    assert rc == 0
    assert captured["timeout"] == 42.0


def test_runner_returns_124_on_timeout(monkeypatch):
    def fake_run(cmd, timeout=None):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    _patch_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    r = runners.CliReindexRunner("chroma", "--chroma")
    rc = r.run("demo", Path("."), {})

    assert rc == runners._TIMEOUT_RC == 124


def test_default_timeout_used_when_unset(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, timeout=None):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("codegraph", "--codegraph").run("demo", Path("."), {})

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)


def test_non_positive_timeout_disables_bound(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, timeout=None):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": 0}}
    )

    assert captured["timeout"] is None


def test_invalid_timeout_falls_back_to_default(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, timeout=None):
        captured["timeout"] = timeout

        class _R:
            returncode = 0

        return _R()

    _patch_venv(monkeypatch)
    monkeypatch.setattr(subprocess, "run", fake_run)

    runners.CliReindexRunner("chroma", "--chroma").run(
        "demo", Path("."), {"reindex": {"runner_timeout_sec": "not-a-number"}}
    )

    assert captured["timeout"] == float(runners._DEFAULT_RUNNER_TIMEOUT_SEC)
